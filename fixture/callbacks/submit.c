#include "callbacks.h"
void submit(tpool_t *pool, void (*cb)(void *)) {
  pushTpool(pool, (task_t){.args = NULL, .func = cb});
}
