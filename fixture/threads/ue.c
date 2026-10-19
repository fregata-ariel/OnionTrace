#include "threads.h"
static void *local(void *arg) { return arg; }
static void init_NR_UE_threads(void *arg) {
  pthread_t a, b;
  threadCreate(&a, local, arg, "UEthread", -1, 1);
  threadCreate(&b, worker, arg, "L1_UE_stats", -1, 1);
}
int main(int argc, char **argv) {
  for (int inst = 0; inst < argc; inst++) init_NR_UE_threads(argv);
  return 0;
}
