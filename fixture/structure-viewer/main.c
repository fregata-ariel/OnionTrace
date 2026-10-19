/* Source-only UI fixture; no OAI runtime observations are claimed. */
#include "queues.h"
static notifiedFIFO_t requests, replies;
static void process(void *input) { if (input) *(int *)input += 1; }
static void *worker(void *input) {
  notifiedFIFO_elt_t *e = pullNotifiedFIFO(&requests);
  if (e) {
    if (e->processingFunc) e->processingFunc(NotifiedFifoData(e));
    if (e->reponseFifo) pushNotifiedFIFO(e->reponseFifo, e);
  }
  return input;
}
static void initialize(int enabled) {
  unsigned long handle;
  initNotifiedFIFO(&requests); initNotifiedFIFO(&replies);
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(sizeof(int), 0, &replies, process);
  pushNotifiedFIFO(&requests, e);
  /* Keep a NULL callback alternative and a condition at the creation site. */
  e = newNotifiedFIFO_elt(sizeof(int), 1, &replies, NULL);
  pushNotifiedFIFO(&requests, e);
  if (enabled) {
#ifdef UE_VIEW
    threadCreate(&handle, worker, NULL, "ue-worker", 0, 0);
#else
    threadCreate(&handle, worker, NULL, "gnb-worker", 0, 0);
#endif
  }
  pullNotifiedFIFO(&replies);
}
int main(int argc, char **argv) {
  initialize(argc > 0);
  /* Unknown pointer stays visible and must not be turned into a callee. */
  if (argc > 1) ((void (*)(void *))argv[1])(NULL);
  return 0;
}
