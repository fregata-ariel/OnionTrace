#include <stddef.h>
#include <stdint.h>
typedef struct notifiedFIFO_elt_s {
  struct notifiedFIFO_elt_s *next;
  uint64_t key;
  struct notifiedFIFO_s *reponseFifo;
  void (*processingFunc)(void *);
  int malloced;
  void *msgData;
} notifiedFIFO_elt_t;
typedef struct notifiedFIFO_s { notifiedFIFO_elt_t *head; } notifiedFIFO_t;
typedef struct { notifiedFIFO_t fifo, reply; } Owner;
void initNotifiedFIFO(notifiedFIFO_t *);
void pushNotifiedFIFO(notifiedFIFO_t *, notifiedFIFO_elt_t *);
notifiedFIFO_elt_t *pullNotifiedFIFO(notifiedFIFO_t *);
notifiedFIFO_elt_t *pollNotifiedFIFO(notifiedFIFO_t *);
void abortNotifiedFIFO(notifiedFIFO_t *);
void delNotifiedFIFO_elt(notifiedFIFO_elt_t *);
notifiedFIFO_elt_t *newNotifiedFIFO_elt(int, uint64_t, notifiedFIFO_t *, void (*)(void *));
void *NotifiedFifoData(notifiedFIFO_elt_t *);
void threadCreate(unsigned long *, void *(*)(void *), void *, char *, int, int);
