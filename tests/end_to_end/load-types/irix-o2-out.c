s32 *test(); // static
extern s8 D_410110;
extern u8 D_410111;
extern s16 D_410112;
extern u16 D_410114;
extern s32 D_410118;
extern s32 D_41011C;
extern s32 D_410120;

s32 *test(void) {
    D_410120.unk0 = (s32) D_410110;
    D_410120.unk4 = (s32) D_410111;
    D_410120.unk8 = (s32) D_410112;
    D_410120.unkC = (s32) D_410114;
    D_410120.unk10 = (s32) D_410118;
    D_410120.unk14 = (s32) D_41011C;
    return &D_410120;
}
