#include "callbacks.h"
void process(void *arg) { (void)arg; }
void alternate(void *arg) { (void)arg; }
extern void (*opaque(void))(void *);
void inline_actor(element_t *elt, int actors) {
  if (actors > 0) pushNotifiedFIFO(NULL, elt);
  else elt->processingFunc(NULL);
}
int main(int argc, char **argv) {
  pthread_t actor_a, actor_b, worker_a, worker_b;
  tpool_t pool = {argc};
  init_actor(&actor_a); init_actor(&actor_b);
  init_pool(&worker_a, &pool); init_pool(&worker_b, &pool);
  element_t *rx = newNotifiedFIFO_elt(0, 0, NULL, process);
  inline_actor(rx, argc);
  element_t *choice = newNotifiedFIFO_elt(0, 0, NULL, argc ? process : alternate);
  pushNotifiedFIFO(NULL, choice);
  element_t *nullable = newNotifiedFIFO_elt(0, 0, NULL, argc ? process : NULL);
  pushNotifiedFIFO(NULL, nullable);
  newNotifiedFIFO_elt(0, 0, NULL, NULL);
  newNotifiedFIFO_elt(0, 0, NULL, opaque());
  pushTpool(&pool, (task_t){.args = argv, .func = process});
  pushTpool(&pool, (task_t){.args = argv, .func = argc ? process : alternate});
  pushTpool(&pool, (task_t){.args = NULL, .func = NULL});
  task_t local = {.args = argv, .func = process};
  local.func = opaque();
  pushTpool(&pool, local);
  submit(&pool, process);
  if (0) pushTpool(&pool, (task_t){.func = alternate});
  return 0;
}
