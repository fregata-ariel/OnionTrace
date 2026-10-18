#include "callbacks.h"
static void process(void *arg) { (void)arg; }
int main(void) {
  tpool_t pool = {0};
  pushTpool(&pool, (task_t){.func = process});
  return 0;
}
