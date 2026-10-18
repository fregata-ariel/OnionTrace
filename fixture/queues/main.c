#include "queues.h"
Owner first, second;
notifiedFIFO_t resp_L1, L1_rx_out;
extern notifiedFIFO_t *unknown_queue(void);
extern void change_queue(notifiedFIFO_t **);
static void process(void *p) { (void)p; }
static void submit(notifiedFIFO_t *q, notifiedFIFO_t *reply) {
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(4, 0, reply, process);
  pushNotifiedFIFO(q, e);
}
static void *actor_thread(void *arg) {
  Owner *owner = arg;
  notifiedFIFO_elt_t *e = pullNotifiedFIFO(&owner->fifo);
  if (e) {
    if (e->processingFunc) e->processingFunc(NotifiedFifoData(e));
    if (e->reponseFifo) pushNotifiedFIFO(e->reponseFifo, e);
    else delNotifiedFIFO_elt(e);
  }
  return NULL;
}
static void rx_func(void *info) {
  (void)info;
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(4, 0, &L1_rx_out, NULL);
  pushNotifiedFIFO(&L1_rx_out, e);
}
static void *L1_rx_thread(void *arg) {
  (void)arg;
  notifiedFIFO_elt_t *e = pullNotifiedFIFO(&resp_L1);
  if (e) { void *info = NotifiedFifoData(e); rx_func(info); delNotifiedFIFO_elt(e); }
  return NULL;
}
void opaque(notifiedFIFO_t *q, int choose) {
  notifiedFIFO_t *p = &first.fifo;
  if (choose) p = q;
  change_queue(&p);
  pushNotifiedFIFO(p, NULL);
  pushNotifiedFIFO(unknown_queue(), NULL);
}
void modified(notifiedFIFO_t *q) {
  q = &second.fifo;
  pushNotifiedFIFO(q, NULL);
}
int main(void) {
  unsigned long a, b, c;
  initNotifiedFIFO(&first.fifo); initNotifiedFIFO(&second.fifo);
  initNotifiedFIFO(&first.reply); initNotifiedFIFO(&second.reply);
  submit(&first.fifo, &first.reply); submit(&second.fifo, &second.reply);
  threadCreate(&a, actor_thread, (void *)&first, "a", 0, 0);
  threadCreate(&b, actor_thread, (void *)&second, "b", 0, 0);
  pullNotifiedFIFO(&first.reply); pollNotifiedFIFO(&second.reply);
  pullNotifiedFIFO(&second.fifo);
  modified(&first.fifo);
  opaque(&first.fifo, 1);
  initNotifiedFIFO(&resp_L1); initNotifiedFIFO(&L1_rx_out);
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(4, 0, &resp_L1, NULL);
  pushNotifiedFIFO(&resp_L1, e);
  threadCreate(&c, L1_rx_thread, NULL, "rx", 0, 0);
  pullNotifiedFIFO(&L1_rx_out);
  abortNotifiedFIFO(&first.fifo);
  if (0) pushNotifiedFIFO(&second.fifo, e);
  return 0;
}
// Opaque sources are defined to allow a normal CMake build, not interpreted as aliases.
notifiedFIFO_t *unknown_queue(void) { return NULL; }
void change_queue(notifiedFIFO_t **q) { (void)q; }
