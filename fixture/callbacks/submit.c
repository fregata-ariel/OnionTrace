#include "callbacks.h"
void submit(tpool_t *pool, void (*cb)(void *)) {
  pushTpool(pool, (task_t){.args = NULL, .func = cb});
}

extern void (*opaque(void))(void *);
extern void alternate(void *);
extern void rewrite_callback(void (**)(void *));

void forward_unknown(tpool_t *pool, task_t task) {
  task.func = opaque();
  pushTpool(pool, task);
}

void forward_alternate(tpool_t *pool, task_t task) {
  task.func = alternate;
  pushTpool(pool, task);
}

void forward_escape(tpool_t *pool, task_t task) {
  rewrite_callback(&task.func);
  pushTpool(pool, task);
}

void forward_unchanged(tpool_t *pool, task_t task) {
  pushTpool(pool, task);
}
