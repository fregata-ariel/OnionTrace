#ifndef CALLBACKS_H
#define CALLBACKS_H
#include <stddef.h>
#include <pthread.h>
typedef struct element { void (*processingFunc)(void *); } element_t;
typedef struct { int len_thr; } tpool_t;
typedef struct { void *args; void (*func)(void *); } task_t;
typedef struct { int success; task_t t; } ret_t;
element_t *newNotifiedFIFO_elt(int, int, void *, void (*)(void *));
void pushNotifiedFIFO(void *, element_t *);
void pushTpool(tpool_t *, task_t);
void init_actor(pthread_t *);
void init_pool(pthread_t *, tpool_t *);
void *actor_thread(void *);
void *worker_thread(void *);
void submit(tpool_t *, void (*)(void *));
void forward_unknown(tpool_t *, task_t);
void forward_alternate(tpool_t *, task_t);
void forward_escape(tpool_t *, task_t);
void forward_unchanged(tpool_t *, task_t);
#endif
