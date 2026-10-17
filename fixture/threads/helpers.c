#include "threads.h"
void *worker(void *arg) { return arg; }
void *other_worker(void *arg) { return arg; }
void launch(pthread_t *t, void *(*entry)(void *), void *arg) {
  pthread_create(t, 0, entry, arg);
}
void nested(pthread_t *t, void *(*entry)(void *), void *arg) {
  launch(t, entry, arg);
}
void mutate(pthread_t *t, void *(*entry)(void *), void *arg) {
  entry = other_worker;
  pthread_create(t, 0, entry, arg);
}
void threadCreate(pthread_t *t, void *(*entry)(void *), void *arg, char *name, int cpu, int priority) {
  (void)name; (void)cpu; (void)priority;
  pthread_create(t, 0, entry, arg);
}
