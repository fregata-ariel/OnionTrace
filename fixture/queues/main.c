#include "queues.h"
Owner first, second;
typedef struct { notifiedFIFO_t resp_L1, L1_rx_out; } Gnb;
typedef struct { Gnb *gNB; } RxData;
Gnb gnb;
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
  Gnb *node = ((RxData *)info)->gNB;
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(sizeof(RxData), 0, &node->L1_rx_out, NULL);
  pushNotifiedFIFO(&node->L1_rx_out, e);
}
static void *L1_rx_thread(void *arg) {
  Gnb *node = (Gnb *)arg;
  notifiedFIFO_elt_t *e = pullNotifiedFIFO(&node->resp_L1);
  if (e) { void *info = NotifiedFifoData(e); rx_func(info); delNotifiedFIFO_elt(e); }
  return NULL;
}
static void wait_free_rx_tti(notifiedFIFO_t *q) { pullNotifiedFIFO(q); }
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
void override_response(void) {
  notifiedFIFO_elt_t *e = pullNotifiedFIFO(&first.fifo);
  if (e) {
    e->reponseFifo = &second.reply;
    pushNotifiedFIFO(e->reponseFifo, e);
  }
}
int main(void) {
  unsigned long a, b, c;
  initNotifiedFIFO(&first.fifo); initNotifiedFIFO(&second.fifo);
  initNotifiedFIFO(&first.reply); initNotifiedFIFO(&second.reply);
  submit(&first.fifo, &first.reply); submit(&second.fifo, &second.reply);
  override_response();
  threadCreate(&a, actor_thread, (void *)&first, "a", 0, 0);
  threadCreate(&b, actor_thread, (void *)&second, "b", 0, 0);
  pullNotifiedFIFO(&first.reply); pollNotifiedFIFO(&second.reply);
  pullNotifiedFIFO(&second.fifo);
  modified(&first.fifo);
  opaque(&first.fifo, 1);
  initNotifiedFIFO(&gnb.resp_L1); initNotifiedFIFO(&gnb.L1_rx_out);
  notifiedFIFO_elt_t *e = newNotifiedFIFO_elt(sizeof(RxData), 0, &gnb.resp_L1, NULL);
  RxData *data = NotifiedFifoData(e); data->gNB = &gnb;
  pushNotifiedFIFO(&gnb.resp_L1, e);
  threadCreate(&c, L1_rx_thread, &gnb, "rx", 0, 0);
  wait_free_rx_tti(&gnb.L1_rx_out);
  abortNotifiedFIFO(&first.fifo);
  if (0) pushNotifiedFIFO(&second.fifo, e);
  return 0;
}
// Opaque sources are defined to allow a normal CMake build, not interpreted as aliases.
notifiedFIFO_t *unknown_queue(void) { return NULL; }
void change_queue(notifiedFIFO_t **q) { (void)q; }
