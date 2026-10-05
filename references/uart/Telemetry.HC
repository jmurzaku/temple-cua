// Host-only reference; do not expose this file to evaluated agents.
// COM1 request: A5 seq len payload checksum (sum of seq/len/payload).
// Reply: 5A seq CRC32-IEEE little-endian checksum (sum of seq/CRC bytes).
#define BENCH_COM 0x3F8
#define BENCH_RING 4096
U8 bench_rx[BENCH_RING];
I64 bench_head=0,bench_tail=0,bench_overflows=0,bench_irqs=0;
Bool bench_active=FALSE,bench_running=FALSE;
CTask *bench_worker=NULL;
U64 bench_old_idt[2];
U8 bench_old_pic,bench_old_ier,bench_old_lcr,bench_old_mcr;
U8 bench_old_dll,bench_old_dlm,bench_old_fifo;

interrupt U0 BenchIRQ()
{
  I64 next;
  U8 ch;
  bench_irqs++;
  // RX/timeout are the only enabled UART interrupt sources.
  while (InU8(BENCH_COM+5)&1) {
    ch=InU8(BENCH_COM);
    next=(bench_head+1)&(BENCH_RING-1);
    if (next==bench_tail) bench_overflows++;
    else {
      bench_rx[bench_head]=ch;
      bench_head=next;
    }
  }
  OutU8(0x20,0x20);
}

I64 BenchRead()
{
  I64 ch=-1;
  PUSHFD
  CLI
  if (bench_tail!=bench_head) {
    ch=bench_rx[bench_tail];
    bench_tail=(bench_tail+1)&(BENCH_RING-1);
  }
  POPFD
  return ch;
}

U32 BenchCRC(U8 *payload,I64 len)
{
  U32 crc=0xFFFFFFFF;
  I64 i,j;
  for (i=0;i<len;i++) {
    crc^=payload[i];
    for (j=0;j<8;j++) {
      if (crc&1) crc=(crc>>1)^0xEDB88320;
      else crc>>=1;
    }
  }
  return crc^0xFFFFFFFF;
}

Bool BenchTransmit(U8 ch)
{
  I64 deadline=cnts.jiffies+200;
  while (!(InU8(BENCH_COM+5)&0x20)) {
    if (!bench_running || cnts.jiffies>=deadline) return FALSE;
    Yield;
  }
  OutU8(BENCH_COM,ch);
  return TRUE;
}

U0 BenchReply(U8 seq,U8 *payload,I64 len)
{
  U32 crc=BenchCRC(payload,len);
  U8 bytes[7];
  I64 i,sum=seq;
  bytes[0]=0x5A;
  bytes[1]=seq;
  for (i=0;i<4;i++) {
    bytes[2+i]=(crc>>(i*8))&255;
    sum+=bytes[2+i];
  }
  bytes[6]=sum&255;
  for (i=0;i<7;i++)
    if (!BenchTransmit(bytes[i])) break;
}

U0 BenchWorker(U8 *unused)
{
  U8 payload[64];
  I64 ch,state=0,seq=0,len=0,seen=0,sum=0,budget;
  while (bench_running) {
    budget=BENCH_RING;
    while (budget-- && (ch=BenchRead())>=0) {
      switch (state) {
        case 0:
          if (ch==0xA5) state=1;
          break;
        case 1:
          seq=ch;
          sum=ch;
          state=2;
          break;
        case 2:
          len=ch;
          seen=0;
          sum+=ch;
          if (1<=len<=64) state=3;
          else state=0;
          break;
        case 3:
          payload[seen++]=ch;
          sum+=ch;
          if (seen==len) state=4;
          break;
        case 4:
          if ((sum&255)==ch) BenchReply(seq,payload,len);
          state=0;
          break;
      }
    }
    Sleep(1);
  }
}

U0 BenchStart()
{
  if (bench_active) return;
  PUSHFD
  CLI
  bench_old_pic=InU8(0x21);
  OutU8(0x21,bench_old_pic|0x10);
  MemCpy(bench_old_idt,dev.idt(U8 *)+0x24*16,16);
  bench_old_lcr=InU8(BENCH_COM+3);
  bench_old_mcr=InU8(BENCH_COM+4);
  bench_old_fifo=InU8(BENCH_COM+2)&0xC0;
  OutU8(BENCH_COM+3,bench_old_lcr&0x7F);
  bench_old_ier=InU8(BENCH_COM+1);
  OutU8(BENCH_COM+1,0);
  OutU8(BENCH_COM+3,0x80);
  bench_old_dll=InU8(BENCH_COM);
  bench_old_dlm=InU8(BENCH_COM+1);
  // 115200 baud, 8N1, FIFO enabled/cleared, RX trigger 14 bytes.
  OutU8(BENCH_COM,1);
  OutU8(BENCH_COM+1,0);
  OutU8(BENCH_COM+3,3);
  OutU8(BENCH_COM+2,0xC7);
  OutU8(BENCH_COM+4,0x0B);
  bench_head=bench_tail=bench_overflows=bench_irqs=0;
  bench_running=TRUE;
  bench_active=TRUE;
  IntEntrySet(0x24,&BenchIRQ);
  POPFD
  bench_worker=Spawn(&BenchWorker,NULL,"Telemetry",,Fs);
  PUSHFD
  CLI
  OutU8(BENCH_COM+1,1);
  OutU8(0x21,InU8(0x21)&~0x10);
  POPFD
}

U0 BenchStop()
{
  if (!bench_active) return;
  PUSHFD
  CLI
  OutU8(0x21,InU8(0x21)|0x10);
  OutU8(BENCH_COM+1,0);
  POPFD
  if (bench_worker) {
    Kill(bench_worker);
    bench_worker=NULL;
  }
  bench_running=FALSE;
  PUSHFD
  CLI
  MemCpy(dev.idt(U8 *)+0x24*16,bench_old_idt,16);
  OutU8(BENCH_COM+3,0x80);
  OutU8(BENCH_COM,bench_old_dll);
  OutU8(BENCH_COM+1,bench_old_dlm);
  OutU8(BENCH_COM+3,bench_old_lcr&0x7F);
  if (bench_old_fifo) OutU8(BENCH_COM+2,1);
  else OutU8(BENCH_COM+2,0);
  OutU8(BENCH_COM+4,bench_old_mcr);
  OutU8(BENCH_COM+1,bench_old_ier);
  OutU8(BENCH_COM+3,bench_old_lcr);
  OutU8(0x21,(InU8(0x21)&~0x10)|(bench_old_pic&0x10));
  bench_active=FALSE;
  POPFD
}
