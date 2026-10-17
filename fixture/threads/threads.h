#include <pthread.h>
void *worker(void *);
void *other_worker(void *);
void launch(pthread_t *, void *(*)(void *), void *);
void nested(pthread_t *, void *(*)(void *), void *);
void mutate(pthread_t *, void *(*)(void *), void *);
void threadCreate(pthread_t *, void *(*)(void *), void *, char *, int, int);
void omitted(void);
