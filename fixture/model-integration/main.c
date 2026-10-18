/* Source extraction fixture. Test observations are explicit synthetic runs. */
#include "queues.h"
/* Volatile symbols give the adapter one semantic DeclRefExpr per probe. */
volatile int outer_begin, inner_begin, left_probe, right_probe, inner_end, outer_end;
notifiedFIFO_t jobs;
static void process(void *input) {
  (void)outer_begin;
  if (input) {
    (void)inner_begin;
    if (*(int *)input) (void)left_probe;
    else (void)right_probe;
    (void)inner_end;
  }
  (void)outer_end;
}
static void *worker(void *input) {
  notifiedFIFO_elt_t *e = pullNotifiedFIFO(&jobs);
  if (e) process(NotifiedFifoData(e));
  return input;
}
int main(void) {
  unsigned long handle;
  int input = 1;
  initNotifiedFIFO(&jobs);
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(sizeof(input), 0, NULL, process);
  pushNotifiedFIFO(&jobs, e);
  threadCreate(&handle, worker, NULL, "worker", 0, 0);
  return 0;
}
