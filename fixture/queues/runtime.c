#include "queues.h"
#include <stdlib.h>
void initNotifiedFIFO(notifiedFIFO_t *q) { q->head = NULL; }
void pushNotifiedFIFO(notifiedFIFO_t *q, notifiedFIFO_elt_t *e) { q->head = e; }
notifiedFIFO_elt_t *pullNotifiedFIFO(notifiedFIFO_t *q) { notifiedFIFO_elt_t *e = q->head; q->head = NULL; return e; }
notifiedFIFO_elt_t *pollNotifiedFIFO(notifiedFIFO_t *q) { return pullNotifiedFIFO(q); }
void delNotifiedFIFO_elt(notifiedFIFO_elt_t *e) { free(e); }
void abortNotifiedFIFO(notifiedFIFO_t *q) { q->head = NULL; }
notifiedFIFO_elt_t *newNotifiedFIFO_elt(int n, uint64_t k, notifiedFIFO_t *r, void (*f)(void *)) {
  notifiedFIFO_elt_t *e = calloc(1, sizeof(*e) + n);
  e->key = k; e->reponseFifo = r; e->processingFunc = f; e->msgData = e + 1; return e;
}
void *NotifiedFifoData(notifiedFIFO_elt_t *e) { return e->msgData; }
void threadCreate(unsigned long *t, void *(*f)(void *), void *a, char *n, int c, int p) {
  (void)t; (void)f; (void)a; (void)n; (void)c; (void)p;
}
