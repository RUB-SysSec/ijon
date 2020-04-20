#pragma once
#ifndef _NO_IJON_IN_ASM
#if defined(__cplusplus)  
extern "C" {
#endif

typedef __INT8_TYPE__  ijon_i8_t;
typedef __INT16_TYPE__ ijon_i16_t;
typedef __INT32_TYPE__ ijon_i32_t;
typedef __INT64_TYPE__ ijon_i64_t;

typedef __UINT8_TYPE__  ijon_u8_t;
typedef __UINT16_TYPE__ ijon_u16_t;
typedef __UINT32_TYPE__ ijon_u32_t;
typedef __UINT64_TYPE__ ijon_u64_t;

typedef __SIZE_TYPE__   ijon_size_t;

void ijon_xor_state(ijon_u32_t);

void ijon_push_state(ijon_u32_t);

void ijon_map_inc(ijon_u32_t);
void ijon_map_set(ijon_u32_t);

ijon_u32_t ijon_strdist(char* a,char* b);
ijon_u32_t ijon_memdist(char* a,char* b, ijon_size_t len);

void ijon_max(ijon_u32_t addr, ijon_u64_t val);

void ijon_min(ijon_u32_t addr, ijon_u64_t val);

ijon_u64_t ijon_simple_hash(ijon_u64_t val);
ijon_u32_t ijon_hashint(ijon_u32_t old, ijon_u32_t val);
ijon_u32_t ijon_hashstr(ijon_u32_t old, char* val);
ijon_u32_t ijon_hashmem(ijon_u32_t old, char* val, ijon_size_t len);

#ifdef _IJON_CGC
#define ijon_hashstack() ijon_hashstack_manual()
#else
#define ijon_hashstack() ijon_hashstack_libgcc()
#endif

void ijon_enable_feedback();
void ijon_disable_feedback();

#define _IJON_CONCAT(x, y) x##y
#define _IJON_UNIQ_NAME() IJON_CONCAT(temp,__LINE__)
#define _IJON_ABS_DIST(x,y) ((x)<(y) ? (y)-(x) : (x)-(y))

#define IJON_BITS(x) ((x==0)?{0}:__builtin_clz(x))
#define IJON_INC(x) ijon_map_inc(ijon_hashstr(__LINE__,__FILE__)^(x))
#define IJON_SET(x) ijon_map_set(ijon_hashstr(__LINE__,__FILE__)^(x))

#define IJON_CTX(x) ({ uint32_t hash = hashstr(__LINE__,__FILE__); ijon_xor_state(hash); __typeof__(x) IJON_UNIQ_NAME() = (x); ijon_xor_state(hash); IJON_UNIQ_NAME(); })

#define IJON_MAX(x) ijon_max(ijon_hashstr(__LINE__,__FILE__),(x))
#define IJON_MIN(x) ijon_max(ijon_hashstr(__LINE__,__FILE__),0xffffffffffffffff-(x))
#define IJON_CMP(x,y) IJON_INC(__builtin_popcount((x)^(y)))
#define IJON_DIST(x,y) ijon_min(ijon_hashstr(__LINE__,__FILE__), _IJON_ABS_DIST(x,y))
#define IJON_STRDIST(x,y) IJON_SET(ijon_hashint(ijon_hashstack(), ijon_strdist(x,y)))

#if defined(__cplusplus)  
}
#endif
#endif
