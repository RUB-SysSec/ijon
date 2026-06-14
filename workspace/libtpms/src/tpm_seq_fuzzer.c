/* AFL fuzz harness for libtpms (TPM 2.0) — COMMAND SEQUENCES.
 *
 * Derived from libtpms's own OSS-Fuzz harness (tests/fuzz.cc): same callbacks,
 * same TPMLIB_Process buffer handling, same state suspend/resume round-trip, same
 * TPM_Free cleanup — so the API usage is correct-by-construction. The ONLY change
 * vs the official harness: instead of one fuzz command, we split the input into a
 * STREAM of TPM commands and feed them in order, so the TPM's command-to-command
 * state machine is exercised (the class-2 barrier IJON targets).
 *
 * NOTE (false-positive avoidance): we do NOT abort on functional TPM return codes.
 * The official harness asserts because its single command is benign; for arbitrary
 * fuzzed *sequences*, non-SUCCESS results are expected (failure mode, bad handles),
 * and asserting would be a harness-induced false crash. Real memory bugs are caught
 * by ASAN. There is NO IJON annotation here — that is the analyst agent's job.
 */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>

#include <libtpms/tpm_types.h>
#include <libtpms/tpm_library.h>
#include <libtpms/tpm_error.h>
#include <libtpms/tpm_memory.h>
#include <libtpms/tpm_nvfilename.h>

/* ---- in-memory NVRAM callbacks (verbatim from tests/fuzz.cc) ---- */
static unsigned char *permall;
static uint32_t permall_length;

static TPM_RESULT mytpm_io_init(void) { return TPM_SUCCESS; }
static TPM_RESULT mytpm_io_getlocality(TPM_MODIFIER_INDICATOR *locModif, uint32_t n)
{ *locModif = 0; return TPM_SUCCESS; }
static TPM_RESULT mytpm_io_getphysicalpresence(TPM_BOOL *phyPres, uint32_t n)
{ *phyPres = FALSE; return TPM_SUCCESS; }

static TPM_RESULT mytpm_nvram_loaddata(unsigned char **data, uint32_t *length,
                                       uint32_t tpm_number, const char *name)
{
    if (!strcmp(name, TPM_PERMANENT_ALL_NAME) && permall) {
        *data = NULL;
        if (TPM_Malloc(data, permall_length) != TPM_SUCCESS) return TPM_FAIL;
        memcpy(*data, permall, permall_length);
        *length = permall_length;
        return TPM_SUCCESS;
    }
    return TPM_RETRY;
}
static TPM_RESULT mytpm_nvram_storedata(const unsigned char *data, uint32_t length,
                                        uint32_t tpm_number, const char *name)
{
    if (!strcmp(name, TPM_PERMANENT_ALL_NAME)) {
        free(permall); permall = NULL;
        if (TPM_Malloc(&permall, length) != TPM_SUCCESS) return TPM_FAIL;
        memcpy(permall, data, length);
        permall_length = length;
    }
    return TPM_SUCCESS;
}

static uint32_t be32(const unsigned char *p)
{ return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }

/* One fuzz iteration: boot a fresh TPM, feed the input as a command SEQUENCE,
 * round-trip the state, tear down. Mirrors fuzz.cc's lifecycle exactly. */
static void run_once(const unsigned char *data, size_t size)
{
    unsigned char *rbuffer = NULL; uint32_t rlength = 0, rtotal = 0;
    unsigned char *vol = NULL, *perm = NULL; uint32_t vol_len = 0, perm_len = 0;
    unsigned char startup[] = {
        0x80,0x01, 0x00,0x00,0x00,0x0c, 0x00,0x00,0x01,0x44, 0x00,0x00
    };
    struct libtpms_callbacks cbs = {
        .sizeOfStruct               = sizeof(struct libtpms_callbacks),
        .tpm_nvram_init             = NULL,
        .tpm_nvram_loaddata         = mytpm_nvram_loaddata,
        .tpm_nvram_storedata        = mytpm_nvram_storedata,
        .tpm_nvram_deletename       = NULL,
        .tpm_io_init                = mytpm_io_init,
        .tpm_io_getlocality         = mytpm_io_getlocality,
        .tpm_io_getphysicalpresence = mytpm_io_getphysicalpresence,
    };
    if (TPMLIB_RegisterCallbacks(&cbs) != TPM_SUCCESS) return;
    if (TPMLIB_ChooseTPMVersion(TPMLIB_TPM_VERSION_2) != TPM_SUCCESS) return;
    if (TPMLIB_MainInit() != TPM_SUCCESS) return;

    /* startup, then the fuzzed command SEQUENCE */
    TPMLIB_Process(&rbuffer, &rlength, &rtotal, startup, sizeof(startup));
    size_t off = 0;
    while (off + 10 <= size) {
        uint32_t cmd_size = be32(data + off + 2);
        if (cmd_size < 10 || off + cmd_size > size) break;
        TPMLIB_Process(&rbuffer, &rlength, &rtotal,
                       (unsigned char *)data + off, cmd_size);
        off += cmd_size;
#ifdef _USE_IJON
        /* analyst agent's class-2 annotation, proposed BLIND (the agent's job,
         * not hand-written): expose the running command SEQUENCE so distinct
         * orderings get distinct feedback. (cast added for compilation.) */
        IJON_STATE(ijon_hashmem(0, (char *)data, off));
#endif
    }

    /* state suspend/resume (exercises the serialization paths, like fuzz.cc) */
    if (TPMLIB_GetState(TPMLIB_STATE_VOLATILE, &vol, &vol_len) == TPM_SUCCESS &&
        TPMLIB_GetState(TPMLIB_STATE_PERMANENT, &perm, &perm_len) == TPM_SUCCESS) {
        TPMLIB_Terminate();
        TPMLIB_SetState(TPMLIB_STATE_PERMANENT, perm, perm_len);
        TPMLIB_SetState(TPMLIB_STATE_VOLATILE, vol, vol_len);
        TPMLIB_MainInit();
    }
    TPMLIB_Terminate();

    TPM_Free(rbuffer); TPM_Free(vol); TPM_Free(perm);
    TPM_Free(permall); permall = NULL;
}

#ifndef __AFL_FUZZ_TESTCASE_LEN
ssize_t fuzz_len; unsigned char fuzz_buf[1 << 16];
#define __AFL_FUZZ_TESTCASE_LEN fuzz_len
#define __AFL_FUZZ_TESTCASE_BUF fuzz_buf
#define __AFL_FUZZ_INIT() void sync(void);
#define __AFL_LOOP(x) ((fuzz_len = read(0, fuzz_buf, sizeof(fuzz_buf))) > 0 ? 1 : 0)
#define __AFL_INIT() sync()
#endif

__AFL_FUZZ_INIT();

int main(void)
{
    __AFL_INIT();
    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;
    while (__AFL_LOOP(10000)) {
        size_t len = __AFL_FUZZ_TESTCASE_LEN;
        run_once(buf, len);
    }
    return 0;
}
