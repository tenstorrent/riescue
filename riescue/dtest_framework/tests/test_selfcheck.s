;#test.name       selfcheck_test
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     any
;#test.summary    Write some known values to selfchecked state so we can check if selfcheck region has that data

#ifndef SETUP_GPR_VAL
#define SETUP_GPR_VAL 0xaabbccddeeff0011
#endif
#ifndef SETUP_VEC_VAL
#define SETUP_VEC_VAL 0xfedcba9876543210
#endif
#ifndef SETUP_FP_VAL
#define SETUP_FP_VAL 0x3ff0000000000000
#endif

#ifndef TEST01_GPR_VAL
#define TEST01_GPR_VAL 0xcafed00dbadcab1e
#endif
#ifndef TEST01_VEC_VAL
#define TEST01_VEC_VAL 0x5555666677778888
#endif
#ifndef TEST01_FP_VAL
#define TEST01_FP_VAL 0x4000000000000000
#endif

#ifndef CLEANUP_GPR_VAL
#define CLEANUP_GPR_VAL 0xdeadc0decafebabe
#endif
#ifndef CLEANUP_VEC_VAL
#define CLEANUP_VEC_VAL 0x9999aaaabbbbcccc
#endif
#ifndef CLEANUP_FP_VAL
#define CLEANUP_FP_VAL 0x4008000000000000
#endif

.section .code, "ax"

#####################
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################
test_setup:
    li x1, SETUP_GPR_VAL
    li t0, SETUP_VEC_VAL
    vsetivli zero, 4, e64, m1, ta, ma
    vmv.v.x v31, t0
    li t0, SETUP_FP_VAL
    fmv.d.x f31, t0
    li t0, 0
    ;#test_passed()


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    li x10, TEST01_GPR_VAL
    li t0, TEST01_VEC_VAL
    vsetivli zero, 4, e64, m1, ta, ma
    vmv.v.x v30, t0
    li t0, TEST01_FP_VAL
    fmv.d.x f30, t0
    li t0, 0
    ;#test_passed()


#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
test_cleanup:
    li x20, CLEANUP_GPR_VAL
    li t0, CLEANUP_VEC_VAL
    vsetivli zero, 4, e64, m1, ta, ma
    vmv.v.x v29, t0
    li t0, CLEANUP_FP_VAL
    fmv.d.x f29, t0
    li t0, 0
    ;#test_passed()

