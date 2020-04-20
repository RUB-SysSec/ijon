/********************************************************************************/
/*										*/
/*			Nuvoton Proprietary Command Emulation			*/
/*			     Written by Ken Goldman				*/
/*		       IBM Thomas J. Watson Research Center			*/
/*	      $Id: ntc2.c 1055 2017-08-08 20:30:09Z kgoldman $			*/
/*										*/
/* (c) Copyright IBM Corporation 2015, 2017					*/
/*										*/
/* All rights reserved.								*/
/* 										*/
/* Redistribution and use in source and binary forms, with or without		*/
/* modification, are permitted provided that the following conditions are	*/
/* met:										*/
/* 										*/
/* Redistributions of source code must retain the above copyright notice,	*/
/* this list of conditions and the following disclaimer.			*/
/* 										*/
/* Redistributions in binary form must reproduce the above copyright		*/
/* notice, this list of conditions and the following disclaimer in the		*/
/* documentation and/or other materials provided with the distribution.		*/
/* 										*/
/* Neither the names of the IBM Corporation nor the names of its		*/
/* contributors may be used to endorse or promote products derived from		*/
/* this software without specific prior written permission.			*/
/* 										*/
/* THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS		*/
/* "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT		*/
/* LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR	*/
/* A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT		*/
/* HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,	*/
/* SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT		*/
/* LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,	*/
/* DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY	*/
/* THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT		*/
/* (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE	*/
/* OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.		*/
/********************************************************************************/

/* This emulation does not store the configuration in NV memory.  Thus, it does not persist.
 */

#include "ntc2_fp.h"

/* config state has some required or default values */

static NTC2_CFG_STRUCT ntc2state = {
    .i2cLoc1_2  =	0xff,
    .i2cLoc3_4  =	0xff,
    .AltCfg 	= 	0x13,
    .Direction 	=  	0x00,
    .PullUp 	=   	0xff,
    .PushPull 	=   	0xff,
    .CFG_A 	=   	0xff,
    .CFG_B 	=   	0xff,
    .CFG_C 	=   	0xff,
    .CFG_D 	=   	0xff,
    .CFG_E 	=   	0xff,
    .CFG_F 	=   	0xff,
    .CFG_G 	=   	0xff,
    .CFG_H 	=   	0xff,
    .CFG_I 	=   	0xff,
    .CFG_J 	=   	0xff,
    .IsValid 	=   	0xaa,
    .IsLocked 	= 	0xff
};

TPM_RC
NTC2_PreConfig(NTC2_PreConfig_In *in)
{
    // Input Validation
    if (ntc2state.IsLocked == 0xaa) {
	return TPM_RC_DISABLED;
    }
    ntc2state = in->preConfig;
    ntc2state.IsLocked = 0xff;	/* cannot be set by PreConfig */
    return TPM_RC_SUCCESS;
}

TPM_RC
NTC2_LockPreConfig(void)
{
    ntc2state.IsLocked = 0xaa;
    return TPM_RC_SUCCESS;
}

TPM_RC
NTC2_GetConfig(NTC2_GetConfig_Out *out)
{
    out->preConfig = ntc2state;
    return TPM_RC_SUCCESS;
}
