import ast
import attr
import re
import string
import sys

from contextlib import contextmanager

import typing
from typing import List, Union, Iterator, Optional, Dict, Callable, Any

from pycparser import c_ast as c

import traceback

@attr.s(frozen=True)
class Register(object):
    register_name: str = attr.ib()

    def is_callee_save(self):
        return re.match('s[0-7]', self.register_name)

    def __str__(self):
        return f'${self.register_name}'

@attr.s(frozen=True)
class GlobalSymbol(object):
    symbol_name: str = attr.ib()

    def __str__(self):
        return self.symbol_name

@attr.s(frozen=True)
class Macro(object):
    macro_name: str = attr.ib()
    argument: 'Argument' = attr.ib()  # forward-declare

    def __str__(self):
        return f'%{self.macro_name}({self.argument})'

@attr.s(frozen=True)
class NumberLiteral(object):
    value: int = attr.ib()

    def __str__(self):
        return hex(self.value)

@attr.s(frozen=True)
class AddressMode(object):
    lhs: Optional[Union[NumberLiteral, Macro]] = attr.ib()
    rhs: 'Argument' = attr.ib()

    def __str__(self):
        if self.lhs is not None:
            return f'{self.lhs}({self.rhs})'
        else:
            return f'({self.rhs})'

@attr.s(frozen=True)
class BinOp(object):
    op: str = attr.ib()
    lhs: 'Argument' = attr.ib()
    rhs: 'Argument' = attr.ib()

    def __str__(self):
        return f'{self.lhs} {self.op} {self.rhs}'

@attr.s(frozen=True)
class JumpTarget(object):
    target: str = attr.ib()

    def __str__(self):
        return f'.{self.target}'

Argument = Union[
    Register,
    GlobalSymbol,
    Macro,
    AddressMode,
    NumberLiteral,
    BinOp,
    JumpTarget
]

valid_word = string.ascii_letters + string.digits + '_'
valid_number = '-x' + string.hexdigits

def parse_word(elems: List[str], valid: str=valid_word) -> str:
    S: str = ''
    while elems and elems[0] in valid:
        S += elems.pop(0)
    return S

def parse_number(elems: List[str]) -> int:
    number_str = parse_word(elems, valid_number)
    return ast.literal_eval(number_str)

# Main parser.
def parse_arg_elems(arg_elems: List[str]) -> Optional[Argument]:
    value: Optional[Argument] = None

    def expect(n):
        g = arg_elems.pop(0)
        if g not in n:
            print(f'Expected one of {list(n)}, got {g} (rest: {arg_elems})')
            sys.exit(1)
        return g

    while arg_elems:
        tok: str = arg_elems[0]
        if tok.isspace():
            # Ignore whitespace.
            arg_elems.pop(0)
        elif tok == '$':
            # Register.
            assert value is None
            arg_elems.pop(0)
            value = Register(parse_word(arg_elems))
        elif tok == '.':
            # A jump target (i.e. a label).
            assert value is None
            arg_elems.pop(0)
            value = JumpTarget(parse_word(arg_elems))
        elif tok == '%':
            # A macro (i.e. %hi(...) or %lo(...)).
            assert value is None
            arg_elems.pop(0)
            macro_name = parse_word(arg_elems)
            assert macro_name in ('hi', 'lo')
            expect('(')
            # Get the argument of the macro (which must exist).
            m = parse_arg_elems(arg_elems)
            assert m is not None
            expect(')')
            # A macro may be the lhs of an AddressMode, so we don't return here.
            value = Macro(macro_name, m)
        elif tok == ')':
            # Break out to the parent of this call, since we are in parens.
            break
        elif tok in ('-' + string.digits):
            # Try a number.
            assert value is None
            value = NumberLiteral(parse_number(arg_elems))
        elif tok == '(':
            # Address mode.
            # There was possibly an offset, so value could be a NumberLiteral or Macro.
            assert value is None or isinstance(value, (NumberLiteral, Macro))
            expect('(')
            # Get what is being dereferenced.
            rhs = parse_arg_elems(arg_elems)
            assert rhs is not None
            expect(')')
            value = AddressMode(value, rhs)
        elif tok in valid_word:
            # Global symbol.
            assert value is None
            value = GlobalSymbol(parse_word(arg_elems))
        elif tok in '>+&':
            # Binary operators, used e.g. to modify global symbols or constants.
            assert isinstance(value, (NumberLiteral, GlobalSymbol))

            if tok == '>':
                expect('>')
                expect('>')
                op = '>>'
            else:
                op = expect('&+')

            rhs = parse_arg_elems(arg_elems)
            # These operators can only use constants as the right-hand-side.
            assert isinstance(rhs, NumberLiteral)
            return BinOp(op, value, rhs)
        else:
            # Unknown.
            print(tok, arg_elems)
            sys.exit(1)

    return value

def parse_arg(arg: str) -> Optional[Argument]:
    arg_elems: List[str] = list(arg)
    return parse_arg_elems(arg_elems)


@attr.s  # TODO: Make frozen again, if appropriate.
class Instruction(object):
    mnemonic: str = attr.ib()
    args: List[Argument] = attr.ib()

    def is_branch_instruction(self):
        return self.mnemonic in ['b', 'beq', 'bne', 'bgez', 'bgtz', 'blez', 'bltz']

    def __str__(self):
        return f'    {self.mnemonic} {", ".join(str(arg) for arg in self.args)}'

def parse_instruction(line: str) -> Instruction:
    # First token is instruction name, rest is args.
    line = line.strip()
    mnemonic, _, args_str = line.partition(' ')
    # Parse arguments.
    args: List[Argument] = list(filter(
        None,
        [parse_arg(arg_str.strip()) for arg_str in args_str.split(',')]))
    return Instruction(mnemonic, args)


@attr.s(frozen=True)
class Label(object):
    name: str = attr.ib()

    def __str__(self):
        return f'  .{self.name}:'

@attr.s
class Function(object):
    name: str = attr.ib()
    body: List[Union[Instruction, Label]] = attr.ib(factory=list)

    def new_label(self, name: str):
        self.body.append(Label(name))

    def new_instruction(self, instruction):
        self.body.append(instruction)

    def __str__(self):
        body = "\n".join(str(item) for item in self.body)
        return f'glabel {self.name}\n{body}'

@attr.s
class Program(object):
    filename: str = attr.ib()
    functions: List[Function] = attr.ib(factory=list)
    current_function: Optional[Function] = attr.ib(default=None, repr=False)

    def new_function(self, name: str):
        self.current_function = Function(name=name)
        self.functions.append(self.current_function)

    def new_instruction(self, instruction: Instruction):
        assert self.current_function is not None
        self.current_function.new_instruction(instruction)

    def new_label(self, label_name):
        self.current_function.new_label(label_name)

    def __str__(self):
        functions_str = '\n\n'.join(str(function) for function in self.functions)
        return f'# {self.filename}\n{functions_str}'

@attr.s(frozen=True)
class Block(object):
    index: int = attr.ib()
    label: Optional[Label] = attr.ib()
    instructions: List[Instruction] = attr.ib(factory=list)

    def __str__(self):
        if self.label:
            name = f'{self.index} ({self.label.name})'
        else:
            name = self.index
        inst_str = '\n'.join(str(instruction) for instruction in self.instructions)
        return f'# {name}\n{inst_str}\n'

# Forward-declare the Node types since they're not defined yet.
def is_loop_edge(node: 'Node', edge: 'Node'):
    # Loops are represented by backwards jumps.
    return edge.block.index < node.block.index

@attr.s(frozen=True)
class BasicNode(object):
    block: Block = attr.ib()
    exit_edge: 'Node' = attr.ib()  # forward-declare type

    def is_loop(self):
        return is_loop_edge(self, self.exit_edge)

    def __str__(self):
        return ''.join([
            f'{self.block}\n',
            f'# {self.block.index} -> {self.exit_edge.block.index}',
            " (loop)" if self.is_loop() else ""])

@attr.s(frozen=True)
class ExitNode(object):
    block: Block = attr.ib()

    def __str__(self):
        return f'{self.block}\n# {self.block.index} -> ret'

@attr.s(frozen=True)
class ConditionalNode(object):
    block: Block = attr.ib()
    conditional_edge: 'Node' = attr.ib()  # forward-declare types
    fallthrough_edge: 'Node' = attr.ib()

    def is_loop(self):
        return is_loop_edge(self, self.conditional_edge)

    def __str__(self):
        return ''.join([
            f'{self.block}\n',
            f'# {self.block.index} -> ',
            f'cond: {self.conditional_edge.block.index}',
            ' (loop)' if self.is_loop() else '',
            ', ',
            f'def: {self.fallthrough_edge.block.index}'])

Node = Union[
    BasicNode,
    ExitNode,
    ConditionalNode
]


@attr.s(frozen=True)
class FlowAnalysis(object):
    nodes: List[Node] = attr.ib()


def do_flow_analysis(function: Function):
    # First build blocks...
    blocks: List[Block] = []

    curr_index: int = 0
    curr_label: Optional[Label] = None
    curr_instructions: List[Instruction] = []

    def new_block():
        nonlocal curr_index, curr_label, curr_instructions

        if len(curr_instructions) == 0:
            return
        block = Block(curr_index, curr_label, curr_instructions)
        curr_label = None
        curr_index += 1
        blocks.append(block)
        curr_instructions = []

    def take_instruction(instruction: Instruction):
        curr_instructions.append(instruction)

    body_iter: Iterator[Union[Instruction, Label]] = iter(function.body)
    for item in body_iter:
        if isinstance(item, Label):
            # Split blocks at labels.
            new_block()
            curr_label = item
        elif isinstance(item, Instruction):
            take_instruction(item)
            if item.is_branch_instruction():
                # Handle delay slot. Take the next instruction before splitting body.
                # The cast is necessary because we know next() must be an Instruction,
                # but mypy does not.
                take_instruction(typing.cast(Instruction, next(body_iter)))
                new_block()
    new_block()

    # Now build edges.

    # Build the ExitNode first to avoid a special case later.
    exit_block: Block = blocks[-1]
    exit_node: ExitNode = ExitNode(exit_block)
    nodes: List[Node] = [exit_node]

    def find_block_by_label(label: JumpTarget):
        for block in blocks:
            if block.label and block.label.name == label.target:
                return block

    def get_block_analysis(block: Block):
        # Don't reanalyze blocks.
        for node in nodes:
            if node.block == block:
                return node
        # Perform the analysis.
        node = do_block_analysis(block)
        nodes.append(node)
        return node

    def do_block_analysis(block: Block) -> Node:
        # Extract branching instructions from this block.
        branches: List[Instruction] = [
            inst for inst in block.instructions if inst.is_branch_instruction()
        ]

        if len(branches) == 0:
            # No branches, i.e. the next block is this node's exit block.
            exit_block = blocks[block.index + 1]

            # Recursively analyze.
            exit_node = get_block_analysis(exit_block)
            return BasicNode(block, exit_node)
        elif len(branches) == 1:
            # There is a branch, so emit a ConditionalNode.
            branch = branches[0]

            # Get the jump target.
            branch_label = branch.args[-1]
            assert isinstance(branch_label, JumpTarget)

            # Get the block associated with the jump target.
            branch_block = find_block_by_label(branch_label)
            assert branch_block is not None

            # Recursively analyze.
            branch_node = get_block_analysis(branch_block)
            is_constant_branch = branch.mnemonic == 'b'
            if is_constant_branch:
                # A constant branch becomes a basic edge to our branch target.
                return BasicNode(block, branch_node)
            else:
                # A conditional branch means the fallthrough block is the next block.
                assert len(blocks) > block.index + 1
                fallthrough_block = blocks[block.index + 1]
                # Recursively analyze this too.
                fallthrough_node = get_block_analysis(fallthrough_block)
                return ConditionalNode(block, branch_node, fallthrough_node)
        else:
            # Shouldn't be possible.
            sys.exit(1)

    # Traverse through the block tree.
    entrance_block = blocks[0]
    get_block_analysis(entrance_block)

    # Sort the nodes chronologically.
    nodes.sort(key=lambda node: node.block.index)
    return FlowAnalysis(nodes)


def decompile(filename: str, f: typing.TextIO) -> Program:
    program: Program = Program(filename)

    for line in f:
        # Strip comments and whitespace
        line = re.sub(r'/\*.*\*/', '', line)
        line = re.sub(r'#.*$', '', line)
        line = line.strip()

        if line == '':
            continue
        elif line.startswith('.') and line.endswith(':'):
            # Label.
            label_name: str = line.strip('.:')
            program.new_label(label_name)
        elif line.startswith('.'):
            # Assembler directive.
            pass
        elif line.startswith('glabel'):
            # Function label.
            function_name: str = line.split(' ')[1]
            program.new_function(function_name)
        else:
            # Instruction.
            instruction: Instruction = parse_instruction(line)
            program.new_instruction(instruction)

    print(program.functions[1])

    print("\n\n### FLOW ANALYSIS")
    flow_analysis: FlowAnalysis = do_flow_analysis(program.functions[1])
    for node in flow_analysis.nodes:
        print(node)

    return program

@attr.s
class Store:
    size: int = attr.ib()
    source: Any = attr.ib()
    dest: Any = attr.ib()
    float: bool = attr.ib(default=False)
    # TODO: Figure out

@attr.s
class TypeHint:
    type: str = attr.ib()
    value: Any = attr.ib()
    # TODO: Figure out

def load_upper(args, reg):
    if isinstance(args[1], BinOp):
        # Something like "lui REG (lhs >> 16)". Just take "lhs".
        assert args[1].op == '>>'
        assert args[1].rhs == NumberLiteral(16)
        return args[1].lhs
    elif isinstance(args[1], NumberLiteral):
        # Something like "lui 0x1", meaning 0x10000. Shift left and return.
        return BinaryOp(left=args[1], op='<<', right=NumberLiteral(16))
    else:
        # Something like "lui REG %hi(arg)", but we got rid of the macro.
        return args[1]

def handle_ori(args, reg):
    if isinstance(args[1], BinOp):
        # Something like "ori REG (lhs & 0xFFFF)". We (hopefully) already
        # handled this above, so return None.
        assert args[1].op == '&'
        assert args[1].rhs == NumberLiteral(0xFFFF)
        return None
    else:
        # Regular bitwise OR.
        return BinaryOp(left=reg[args[0]], op='<', right=args[1])

@attr.s
class LocalVar:
    location: int = attr.ib()
    # TODO: Complete

def handle_addi(args, reg):
    if len(args) == 2:
        # Used to be "addi REG %lo(...)", but we got rid of the macro.
        # Return the former argument of the macro.
        return args[1]
    elif args[1].register_name == 'sp':
        # Adding to sp, i.e. passing an address.
        assert isinstance(args[2], NumberLiteral)
        #return UnaryOp(op='&', expr=LocalVar(args[2]))
        return UnaryOp(op='&', expr=AddressMode(lhs=args[2], rhs=Register('sp')))
    else:
        # Regular binary addition.
        return BinaryOp(left=reg[args[1]], op='+', right=args[2])


def deref(arg, reg):
    if isinstance(arg, AddressMode):
        assert isinstance(arg.rhs, Register)
        if arg.rhs.register_name == 'sp':
            #return LocalVar(location=arg.lhs)
            return arg
        else:
            return AddressMode(lhs=arg.lhs, rhs=reg[arg.rhs])
    else:
        assert isinstance(arg, Register)
        return reg[arg]

@attr.s
class BinaryOp:
    left = attr.ib()
    op: str = attr.ib()
    right = attr.ib()

@attr.s
class UnaryOp:
    op: str = attr.ib()
    expr = attr.ib()

@attr.s
class Cast:
    to_type: str = attr.ib()
    expr = attr.ib()

class Return:
    pass

def translate_to_ast(function: Function):
    # Initialize info about the function.
    allocated_stack_size: int = 0
    is_leaf: bool = True

    local_vars_region_bottom: int = 0
    return_addr_location: int = 0
    callee_save_reg_locations: Dict[Register, int] = {}

    def find_stack_info(flow_analysis: FlowAnalysis) -> None:
        nonlocal allocated_stack_size, is_leaf, local_vars_region_bottom
        nonlocal return_addr_location, callee_save_reg_locations

        start_node: Node = flow_analysis.nodes[0]
        for inst in start_node.block.instructions:
            if not inst.args:
                continue

            destination = typing.cast(Register, inst.args[0])

            if inst.mnemonic == 'addiu' and destination.register_name == 'sp':
                # Moving the stack pointer.
                assert isinstance(inst.args[2], NumberLiteral)
                allocated_stack_size = -inst.args[2].value
            elif inst.mnemonic == 'sw' and destination.register_name == 'ra':
                # Saving the return address on the stack.
                assert isinstance(inst.args[1], AddressMode)
                assert isinstance(inst.args[1].rhs, Register)
                assert inst.args[1].rhs.register_name == 'sp'
                is_leaf = False
                if inst.args[1].lhs:
                    assert isinstance(inst.args[1].lhs, NumberLiteral)
                    return_addr_location = inst.args[1].lhs.value
                else:
                    # Note that this should only happen in the rare case that
                    # this function only calls subroutines with no arguments.
                    return_addr_location = 0
            elif (inst.mnemonic == 'sw' and
                  destination.is_callee_save() and
                  isinstance(inst.args[1], AddressMode) and
                  isinstance(inst.args[1].rhs, Register) and
                  inst.args[1].rhs.register_name == 'sp'):
                # Initial saving of callee-save register onto the stack.
                assert isinstance(inst.args[1].rhs, Register)
                if inst.args[1].lhs:
                    assert isinstance(inst.args[1].lhs, NumberLiteral)
                    callee_save_reg_locations[destination] = inst.args[1].lhs.value
                else:
                    callee_save_reg_locations[destination] = 0

        if is_leaf and callee_save_reg_locations:
            # In a leaf with callee-save registers, the local variables
            # lie directly above those registers.
            local_vars_region_bottom = max(callee_save_reg_locations.values()) + 4
        elif is_leaf:
            # In a leaf without callee-save registers, the local variables
            # lie directly at the bottom of the stack.
            local_vars_region_bottom = 0
        else:
            # In a non-leaf, the local variables lie above the location of the
            # return address.
            local_vars_region_bottom = return_addr_location + 4

    def in_local_var_region(location: int) -> bool:
        return local_vars_region_bottom <= location < allocated_stack_size

    def translate_block_body(block: Block, reg: Dict[Register, Any]):
        cases_source_first_expression = {
            # Storage instructions
            'sb': lambda a: Store(size=8, source=reg[a[0]], dest=deref(a[1], reg)),
            'sh': lambda a: Store(size=16, source=reg[a[0]], dest=deref(a[1], reg)),
            'sw': lambda a: Store(size=32, source=reg[a[0]], dest=deref(a[1], reg)),
            # Floating point storage/conversion
            'swc1': lambda a: Store(size=32, source=reg[a[0]], dest=deref(a[1], reg), float=True),
            'sdc1': lambda a: Store(size=64, source=reg[a[0]], dest=deref(a[1], reg), float=True),
        }
        cases_source_first_register = {
            # Floating point moving instruction
            'mtc1': lambda a: Cast(to_type='f32', expr=reg[a[0]]),
        }
        cases_branches = {  # TODO! These are wrong.
            # Branch instructions/pseudoinstructions
            'b': lambda a: a[1],
            'beq': lambda a: BinaryOp(left=reg[a[0]], op='==', right=reg[a[1]]),
            'bne': lambda a: BinaryOp(left=reg[a[0]], op='!=', right=reg[a[1]]),
            'beqz': lambda a: BinaryOp(left=reg[a[0]], op='==', right=NumberLiteral(0)),
            'bnez': lambda a: BinaryOp(left=reg[a[0]], op='!=', right=NumberLiteral(0)),
            'blez': lambda a: BinaryOp(left=reg[a[0]], op='<=', right=NumberLiteral(0)),
            'bgtz': lambda a: BinaryOp(left=reg[a[0]], op='>', right=NumberLiteral(0)),
            'bltz': lambda a: BinaryOp(left=reg[a[0]], op='<', right=NumberLiteral(0)),
            'bgez': lambda a: BinaryOp(left=reg[a[0]], op='>=', right=NumberLiteral(0)),
        }
        cases_float_branches = {  # TODO! Absolutely incomplete.
            # Floating-point branch instructions
            'bc1t': lambda a: a,
            'bc1f': lambda a: a,
        }
        cases_jumps = {
            # Unconditional jumps
            'jal': lambda a: a[0],  # not sure what arguments!
            'jr': lambda a: Return()  # not sure what to return!
        }
        cases_float_comp = {
            # Floating point comparisons
            'c.eq.s': lambda a: BinaryOp(left=reg[a[0]], op='==', right=reg[a[1]]),
            'c.le.s': lambda a: BinaryOp(left=reg[a[0]], op='<=', right=reg[a[1]]),
            'c.lt.s': lambda a: BinaryOp(left=reg[a[0]], op='<', right=reg[a[1]]),
        }
        cases_special = {
            # Handle these specially to get better debug output.
            # These should be unspecial'd at some point by way of an initial
            # pass-through, similar to the stack-info acquisition step.
            'lui': lambda a: load_upper(a, reg),
            'ori': lambda a: handle_ori(a, reg),
            'addi': lambda a: handle_addi(a, reg),
        }
        cases_destination_first = {
            # Flag-setting instructions
            'slt': lambda a: BinaryOp(left=reg[a[1]], op='<', right=reg[a[2]]),
            'slti': lambda a: BinaryOp(left=reg[a[1]], op='<', right=a[2]),
            # LRU (non-floating)
            'addu': lambda a: BinaryOp(left=reg[a[1]], op='+', right=reg[a[2]]),
            'multu': lambda a: BinaryOp(left=reg[a[1]], op='*', right=reg[a[2]]),
            'subu': lambda a: BinaryOp(left=reg[a[1]], op='-', right=reg[a[2]]),
            'div': lambda a: (BinaryOp(left=reg[a[1]], op='/', right=reg[a[2]]),  # hi
                              BinaryOp(left=reg[a[1]], op='%', right=reg[a[2]])), # lo
            'negu': lambda a: UnaryOp(op='-', expr=reg[a[1]]),
            # Hi/lo register uses (used after division)
            'mfhi': lambda a: reg[Register("hi")],
            'mflo': lambda a: reg[Register("lo")],
            # Floating point arithmetic
            'div.s': lambda a: BinaryOp(left=reg[a[1]], op='/', right=reg[a[2]]),
            # Floating point conversions
            'cvt.d.s': lambda a: Cast(to_type='(f64)', expr=reg[a[1]]),
            'cvt.s.d': lambda a: Cast(to_type='(f32)', expr=reg[a[1]]),
            'cvt.w.d': lambda a: Cast(to_type='(s32)', expr=reg[a[1]]),
            'trunc.w.s': lambda a: Cast(to_type='(s32)', expr=reg[a[1]]),
            'trunc.w.d': lambda a: Cast(to_type='(s32)', expr=reg[a[1]]),
            # Bit arithmetic
            'and': lambda a: BinaryOp(left=reg[a[1]], op='&', right=reg[a[2]]),
            'or': lambda a: BinaryOp(left=reg[a[1]], op='^', right=reg[a[2]]),
            'xor': lambda a: BinaryOp(left=reg[a[1]], op='^', right=reg[a[2]]),

            'andi': lambda a: BinaryOp(left=reg[a[1]], op='&', right=a[2]),
            'xori': lambda a: BinaryOp(left=reg[a[1]], op='^', right=a[2]),
            'sll': lambda a: BinaryOp(left=reg[a[1]], op='<<', right=a[2]),
            'srl': lambda a: BinaryOp(left=reg[a[1]], op='>>', right=a[2]),
            # Move pseudoinstruction
            'move': lambda a: reg[a[1]],
            # Floating point moving instructions
            'mfc1': lambda a: reg[a[1]],
            # Loading instructions
            'li': lambda a: a[1],
            'lb': lambda a: TypeHint(type='s8', value=deref(a[1], reg)),
            'lh': lambda a: TypeHint(type='s16', value=deref(a[1], reg)),
            'lw': lambda a: TypeHint(type='s32', value=deref(a[1], reg)),
            'lbu': lambda a: TypeHint(type='u8', value=deref(a[1], reg)),
            'lhu': lambda a: TypeHint(type='u16', value=deref(a[1], reg)),
            'lwu': lambda a: TypeHint(type='u32', value=deref(a[1], reg)),
            # Floating point loading instructions
            'lwc1': lambda a: TypeHint(type='f32', value=deref(a[1], reg)),
            'ldc1': lambda a: TypeHint(type='f64', value=deref(a[1], reg)),
        }
        cases_uniques: Dict[str, Callable[[List[Argument]], Any]] = {
            **cases_source_first_expression,
            **cases_source_first_register,
            **cases_branches,
            **cases_float_branches,
            **cases_jumps,
            **cases_float_comp,
            **cases_special,
            **cases_destination_first,
        }
        cases_repeats = {
            # Addition and division, unsigned vs. signed, doesn't matter (?)
            'addiu': 'addi',
            'divu': 'div',
            # Single-precision float addition is the same as regular addition.
            'add.s': 'addu',
            'mul.s': 'multu',
            'sub.s': 'subu',
            # TODO: These are absolutely not the same as their below-listed
            # counterparts. However, it is hard to tell how to deal with doubles.
            'add.d': 'addu',
            'div.d': 'div.s',
            'mul.d': 'mulu',
            'sub.d': 'subu',
            # Casting (the above applies here too)
            'cvt.d.w': 'cvt.d.s',
            'cvt.s.w': 'cvt.s.d',
            'cvt.w.s': 'cvt.w.d',
            # Floating point comparisons (the above also applies)
            'c.lt.d': 'c.lt.s',
            'c.eq.d': 'c.eq.s',
            'c.le.d': 'c.le.s',
            # Right-shifting.
            'sra': 'srl',
            # Flag setting.
            'sltiu': 'slti',
            'sltu': 'slt',
        }

        to_store: List[Store] = []
        for instr in block.instructions:
            mnemonic = instr.mnemonic
            if mnemonic in cases_repeats:
                # Determine "true" mnemonic.
                mnemonic = cases_repeats[mnemonic]

            if mnemonic == 'nop':
                continue

            # HACK: Preprocessing: remove any macros.
            instr = Instruction(
                mnemonic, list(map(
                    lambda arg: arg.argument if isinstance(arg, Macro) else arg,
                    instr.args
            )))

            # Figure out what code to generate!
            # TODO: This is a side-note for when I inevitably forget to do it,
            # but $zero should not be overwritten by div or anything like that.

            if mnemonic in cases_source_first_expression:
                # Store a value in a permanent place.
                to_store.append(cases_source_first_expression[mnemonic](instr.args))
            elif mnemonic in cases_source_first_register:
                # Just 'mtc1'. It's reversed, so we have to specially handle it.
                assert isinstance(instr.args[1], Register)  # could also assert float register
                reg[instr.args[1]] = cases_source_first_register[mnemonic](instr.args)
            elif mnemonic in cases_branches:
                # TODO: Don't give up here.
                pass
            elif mnemonic in cases_float_branches:
                # TODO: Don't give up here.
                pass
            elif mnemonic in cases_jumps:
                result = cases_jumps[mnemonic](instr.args)
                if isinstance(result, Return):
                    # Return from the function.
                    assert mnemonic == 'jr'
                    # TODO: Maybe assert ExitNode?
                    # TODO: Figure out what to return. (Look through $v0 and $f0)
                else:
                    # Function call. Well, let's double-check:
                    assert mnemonic == 'jal'
                    # TODO: Handle arguments... c.FuncCall(result, ???)
                    pass
            elif mnemonic in cases_float_comp:
                # TODO: Don't give up here. (Similar to branches.)
                pass
            elif mnemonic in cases_special:
                # TODO: One big problem here is that I'm storing the value
                # into the FIRST (intermediate) register, not the final register
                # as I should be! Whoops. This would be fixable right here
                # in this if-statement (and an extra local var and flag).
                assert isinstance(instr.args[0], Register)
                reg[instr.args[0]] = cases_special[mnemonic](instr.args)
            elif mnemonic in cases_destination_first:
                # Hey, I can do this one! (Maybe.)
                assert isinstance(instr.args[0], Register)
                reg[instr.args[0]] = cases_destination_first[mnemonic](instr.args)
            else:
                assert False, f"I don't know how to handle {mnemonic}!"
        print(f"To store: {to_store}")
        print(f"Final register states: {reg}")


    flow_analysis: FlowAnalysis = do_flow_analysis(function)
    find_stack_info(flow_analysis)
    print(f'Stack size: {allocated_stack_size}')
    print(f'Leaf?: {is_leaf}')
    print(f'Local vars region bottom: {local_vars_region_bottom}')
    print(f'Return addr location: {return_addr_location}')
    print(f'Callee save regs: {callee_save_reg_locations}')

    print('\nNow, we attempt to translate:')
    for i, node in enumerate(flow_analysis.nodes):
        if i == 0:
            continue
        print(f'\nblock in question: {node.block}')
        try:
            translate_block_body(node.block, {Register('zero'): NumberLiteral(0)})
        except Exception as e:
            traceback.print_exc()


def main(filename: str) -> None:
    with open(filename, 'r') as f:
        program: Program = decompile(filename, f)
        translate_to_ast(program.functions[1])

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] in ['-h, --help']:
        print(f"USAGE: {sys.argv[0]} [filename]")
    else:
        main(sys.argv[1])
