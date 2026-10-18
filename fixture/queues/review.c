#include "queues.h"

// Keep the two reviewed cases separate from the earlier consumer-side override.
notifiedFIFO_t producer_work, producer_forward_work;
notifiedFIFO_t producer_reply_A, producer_reply_B, producer_unrelated;
static void producer_process(void *data) { (void)data; }
static void forward_element(notifiedFIFO_t *work, notifiedFIFO_elt_t *element) {
  pushNotifiedFIFO(work, element);
}
static void producer_override(int change, int forward) {
  notifiedFIFO_elt_t *element = newNotifiedFIFO_elt(0, 0, &producer_reply_A, producer_process);
  if (change)
    element->reponseFifo = &producer_reply_B;
  // A different element's write must not be carried by this submission.
  notifiedFIFO_elt_t *other = newNotifiedFIFO_elt(0, 0, &producer_unrelated, NULL);
  other->reponseFifo = &producer_unrelated;
  if (forward)
    forward_element(&producer_forward_work, element);
  else
    pushNotifiedFIFO(&producer_work, element);
}
static void producer_response(notifiedFIFO_t *work) {
  notifiedFIFO_elt_t *got = pullNotifiedFIFO(work);
  if (got && got->reponseFifo)
    pushNotifiedFIFO(got->reponseFifo, got);
}

typedef struct { notifiedFIFO_t *reply; } ReplyOwner;
ReplyOwner reply_slots[2], other_reply_slots[1];
notifiedFIFO_t array_reply_A, array_reply_B, array_reply_C;
static ReplyOwner *unknown_slot(void) { return NULL; }
static void array_pointer_writes(int index) {
  reply_slots[0].reply = &array_reply_A;
  reply_slots[1].reply = &array_reply_B;
  other_reply_slots[0].reply = &array_reply_C;
  pushNotifiedFIFO(reply_slots[0].reply, NULL);
  pullNotifiedFIFO(&array_reply_A);
  pullNotifiedFIFO(&array_reply_B);
  pullNotifiedFIFO(&array_reply_C);
  // Neither a dynamic index nor a computed base identifies exact write storage.
  reply_slots[index].reply = &array_reply_C;
  pushNotifiedFIFO(reply_slots[index].reply, NULL);
  unknown_slot()->reply = &array_reply_C;
  pushNotifiedFIFO(unknown_slot()->reply, NULL);
}

void queue_review_cases(void) {
  producer_override(1, 0);
  producer_response(&producer_work);
  producer_response(&producer_forward_work);
  pullNotifiedFIFO(&producer_reply_A);
  pullNotifiedFIFO(&producer_reply_B);
  pullNotifiedFIFO(&producer_unrelated);
  array_pointer_writes(0);
}
