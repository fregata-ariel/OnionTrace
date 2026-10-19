#include "threads.h"
static void *local(void *arg) { return arg; }
int main(int argc, char **argv) {
  pthread_t a, b, c, d;
  pthread_create(&a, 0, worker, argv);
  nested(&b, &worker, argv);
  if (argc > 1) {
    threadCreate(&c, local, argv, "L1_rx_thread", -1, 1);
  } else {
    threadCreate(&d, worker, argv, "L1_tx_thread", -1, 1);
  }
  for (int i = 0; i < argc; ++i) launch(&a, worker, argv);
  pthread_create(&b, 0, argc > 2 ? worker : other_worker, argv);
  void *(*unknown)(void *) = worker;
  pthread_create(&c, 0, unknown, argv);
  mutate(&d, worker, argv);
  if (argc > 4) omitted();
  void (*indirect_init)(pthread_t *, void *(*)(void *), void *) = nested;
  indirect_init(&a, worker, argv);
  if (0) pthread_create(&a, 0, worker, argv);
  return 0;
}
