#include "callbacks.h"
static element_t storage;
element_t *newNotifiedFIFO_elt(int size, int key, void *response, void (*cb)(void *)) {
  storage.processingFunc = cb;
  return &storage;
}
void pushNotifiedFIFO(void *fifo, element_t *elt) { (void)fifo; (void)elt; }
void *actor_thread(void *arg) {
  element_t *elt = arg;
  if (elt->processingFunc) elt->processingFunc(arg);
  return NULL;
}
extern ret_t try_pop_not_q(void *);
extern ret_t pop_not_q(void *);
void *worker_thread(void *arg) {
  ret_t ret;
  for (int i = 0; i < 4; i++) {
    ret = try_pop_not_q(arg);
    if (ret.success) break;
  }
  if (!ret.success) ret = pop_not_q(arg);
  if (ret.t.func == NULL && ret.t.args == NULL) return NULL;
  ret.t.func(ret.t.args);
  return NULL;
}
void pushTpool(tpool_t *pool, task_t task) {
  if (pool->len_thr == 0) { task.func(task.args); return; }
  /* Deferred queue operation belongs to T6. */
}
void init_actor(pthread_t *thread) { pthread_create(thread, NULL, actor_thread, NULL); }
void init_pool(pthread_t *thread, tpool_t *pool) { pthread_create(thread, NULL, worker_thread, pool); }
