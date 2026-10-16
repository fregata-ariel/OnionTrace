#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef BUILD_ID
#define BUILD_ID "fixture-unversioned"
#endif
#ifndef TRACE_CAPACITY
#define TRACE_CAPACITY 8192
#endif
#if TRACE_CAPACITY < 1
#error TRACE_CAPACITY must be positive
#endif

/* All storage exists before execution. The observed job path performs no
 * allocation, formatting or I/O. Its clock and observation costs are NOT a
 * demonstrated real-time bound. Queue synchronization is fixture behavior. */
enum { MAX_JOBS = 1024, QUEUE_CAPACITY = 8 };
typedef enum {
  S_WORK_BEGIN, S_WORK_END, S_BRANCH_SLOW, S_BRANCH_FAST, S_RETRY_OK,
  S_RETRY_FAIL, S_MAIN_BEGIN, S_THREAD_SPAWN, S_THREAD_BEGIN,
  S_QUEUE_ENQUEUE, S_QUEUE_DEQUEUE, S_THREAD_END, S_MAIN_END, S_COUNT
} site_t;
static const char *const site_names[S_COUNT] = {
  "work.begin", "work.end", "branch.slow", "branch.fast", "retry.ok",
  "retry.fail", "main.begin", "thread.spawn", "thread.begin",
  "queue.enqueue", "queue.dequeue", "thread.end", "main.end"
};
typedef struct { int id; uint64_t input; } job_t;
typedef struct {
  uint64_t timestamp_ns, thread_seq, value;
  site_t site;
  int job;
} event_t;
typedef struct {
  event_t events[TRACE_CAPACITY];
  size_t count;
  uint64_t attempted, dropped;
  bool clock_error;
} trace_buffer_t;
typedef struct {
  pthread_mutex_t mutex;
  pthread_cond_t available, space;
  job_t jobs[QUEUE_CAPACITY];
  size_t head, count;
  bool closed;
} queue_t;
static trace_buffer_t main_trace, worker_trace;
static _Thread_local trace_buffer_t *local_trace;
static bool enabled[S_COUNT];
static queue_t queue = {
  .mutex = PTHREAD_MUTEX_INITIALIZER,
  .available = PTHREAD_COND_INITIALIZER,
  .space = PTHREAD_COND_INITIALIZER
};
static uint64_t results[MAX_JOBS];

static void checked(int error) {
  /* Abort on an unrecoverable pthread API error without formatting in the
   * observed path. Normal CLI errors are reported before threads start. */
  if (error != 0) abort();
}

static void observe(site_t site, int job, uint64_t value) {
  if (!enabled[site]) return;
  trace_buffer_t *t = local_trace;
  uint64_t sequence = t->attempted++;
  if (t->count == TRACE_CAPACITY) { ++t->dropped; return; }
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
    t->clock_error = true;
    ++t->dropped;
    return;
  }
  event_t *event = &t->events[t->count++];
  *event = (event_t){
    .timestamp_ns = (uint64_t)ts.tv_sec * UINT64_C(1000000000) + (uint64_t)ts.tv_nsec,
    .thread_seq = sequence, .value = value, .site = site, .job = job
  };
}

/* This is the acyclic CFG region extracted by Clang: exactly three paths.
 * Keep direct observe(S_..., ...) calls as machine-readable site anchors.
 * Arithmetic is unsigned and deterministic, including overflow. */
static uint64_t process_job(const job_t *job) {
  uint64_t x = job->input;
  uint64_t answer;
  observe(S_WORK_BEGIN, job->id, x);
  if (x % 3 == 0) {
    observe(S_BRANCH_FAST, job->id, x);
    answer = x * 3 + 1;
  } else {
    observe(S_BRANCH_SLOW, job->id, x);
    answer = x * 5 + 7;
    if (x % 3 == 1) {
      observe(S_RETRY_OK, job->id, answer);
      answer += 11;
    } else {
      observe(S_RETRY_FAIL, job->id, answer);
      answer ^= UINT64_C(0x55);
    }
  }
  observe(S_WORK_END, job->id, answer);
  return answer;
}

static void enqueue(job_t job) {
  checked(pthread_mutex_lock(&queue.mutex));
  while (queue.count == QUEUE_CAPACITY)
    checked(pthread_cond_wait(&queue.space, &queue.mutex));
  queue.jobs[(queue.head + queue.count) % QUEUE_CAPACITY] = job;
  /* Capture ownership transfer while holding the existing queue lock,
   * before the consumer can acquire it and observe the newly visible job. */
  observe(S_QUEUE_ENQUEUE, job.id, job.input);
  ++queue.count;
  checked(pthread_cond_signal(&queue.available));
  checked(pthread_mutex_unlock(&queue.mutex));
}

static bool dequeue(job_t *job) {
  checked(pthread_mutex_lock(&queue.mutex));
  while (queue.count == 0 && !queue.closed)
    checked(pthread_cond_wait(&queue.available, &queue.mutex));
  if (queue.count == 0) {
    checked(pthread_mutex_unlock(&queue.mutex));
    return false;
  }
  *job = queue.jobs[queue.head];
  queue.head = (queue.head + 1) % QUEUE_CAPACITY;
  --queue.count;
  observe(S_QUEUE_DEQUEUE, job->id, job->input);
  checked(pthread_cond_signal(&queue.space));
  checked(pthread_mutex_unlock(&queue.mutex));
  return true;
}

static void *worker_main(void *unused) {
  (void)unused;
  local_trace = &worker_trace;
  observe(S_THREAD_BEGIN, -1, 0);
  job_t job;
  while (dequeue(&job)) results[job.id] = process_job(&job);
  observe(S_THREAD_END, -1, 0);
  return NULL;
}

static void json_string(const char *s) {
  putchar('"');
  for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
    if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
    else if (*p < 0x20) printf("\\u%04x", (unsigned)*p);
    else putchar(*p);
  }
  putchar('"');
}

static bool parse_uint64(const char *s, uint64_t *out) {
  if (!*s) return false;
  for (const char *p = s; *p; ++p) if (*p < '0' || *p > '9') return false;
  errno = 0;
  char *end;
  unsigned long long value = strtoull(s, &end, 10);
  if (errno || *end || value > UINT64_MAX) return false;
  *out = (uint64_t)value;
  return true;
}

static bool parse_sites(const char *s) {
  if (!*s) return true;
  const char *begin = s;
  for (;;) {
    const char *end = strchr(begin, ',');
    size_t length = end ? (size_t)(end - begin) : strlen(begin);
    int match = -1;
    for (int i = S_WORK_BEGIN; i <= S_RETRY_FAIL; ++i)
      if (strlen(site_names[i]) == length && !strncmp(begin, site_names[i], length)) match = i;
    if (match < 0 || enabled[match]) return false;
    enabled[match] = true;
    if (!end) break;
    begin = end + 1;
  }
  return true;
}

static int usage(const char *problem) {
  if (problem) fprintf(stderr, "fixture: %s\n", problem);
  fprintf(stderr,
    "Usage: fixture --run-id R --plan-id P [--sites work.begin,work.end,...]\n"
    "               [--seed N] [--jobs N]\n"
    "Sites: work.begin, work.end, branch.slow, branch.fast, retry.ok, retry.fail\n"
    "An empty site list is allowed; lifecycle/queue sites are always recorded.\n"
    "Defaults: sites=work.begin,work.end seed=0 jobs=12; jobs range=1..1024.\n");
  return problem ? 2 : 0;
}

static void emit_event(const event_t *e, const char *thread, size_t *sequence) {
    if (*sequence) putchar(',');
    printf("{\"seq\":%zu,\"thread\":", (*sequence)++);
    json_string(thread);
    printf(",\"thread_seq\":%" PRIu64 ",\"timestamp_ns\":%" PRIu64 ",\"site\":",
           e->thread_seq, e->timestamp_ns);
    json_string(site_names[e->site]);
    printf(",\"job\":%d,\"value\":%" PRIu64, e->job, e->value);
    if (e->site == S_QUEUE_ENQUEUE || e->site == S_QUEUE_DEQUEUE)
      printf(",\"queue\":\"jobs\"");
    putchar('}');
}

int main(int argc, char **argv) {
  const char *run_id = NULL, *plan_id = NULL, *sites = "work.begin,work.end";
  uint64_t seed = 0, jobs = 12;
  unsigned seen = 0;
  for (int i = 1; i < argc; ++i) {
    if (!strcmp(argv[i], "--help") && argc == 2) return usage(NULL);
    unsigned option;
    if (!strcmp(argv[i], "--run-id")) option = 1;
    else if (!strcmp(argv[i], "--plan-id")) option = 2;
    else if (!strcmp(argv[i], "--sites")) option = 4;
    else if (!strcmp(argv[i], "--seed")) option = 8;
    else if (!strcmp(argv[i], "--jobs")) option = 16;
    else return usage("unknown option");
    if (seen & option) return usage("duplicate option");
    seen |= option;
    if (++i == argc) return usage("option is missing its value");
    if (option == 1) run_id = argv[i];
    else if (option == 2) plan_id = argv[i];
    else if (option == 4) sites = argv[i];
    else if (option == 8 && !parse_uint64(argv[i], &seed)) return usage("invalid seed");
    else if (option == 16 && !parse_uint64(argv[i], &jobs)) return usage("invalid jobs");
  }
  if (!run_id || !*run_id || !plan_id || !*plan_id)
    return usage("nonempty --run-id and --plan-id are required");
  if (jobs < 1 || jobs > MAX_JOBS) return usage("jobs must be between 1 and 1024");
  if (!parse_sites(sites)) return usage("invalid or duplicate site");
  for (int i = S_MAIN_BEGIN; i < S_COUNT; ++i) enabled[i] = true;

  memset(&main_trace, 0, sizeof(main_trace));
  memset(&worker_trace, 0, sizeof(worker_trace));
  local_trace = &main_trace;
  observe(S_MAIN_BEGIN, -1, 0);
  observe(S_THREAD_SPAWN, -1, 0);
  pthread_t worker;
  checked(pthread_create(&worker, NULL, worker_main, NULL));
  for (uint64_t i = 0; i < jobs; ++i)
    enqueue((job_t){ .id = (int)i, .input = seed + i });
  checked(pthread_mutex_lock(&queue.mutex));
  queue.closed = true;
  checked(pthread_cond_signal(&queue.available));
  checked(pthread_mutex_unlock(&queue.mutex));
  checked(pthread_join(worker, NULL));
  observe(S_MAIN_END, -1, 0);

  /* Validate computation independently after join. These values are useful
   * diagnostics, never evidence used by the path reconstruction algorithm. */
  for (uint64_t i = 0; i < jobs; ++i) {
    uint64_t x = seed + i;
    uint64_t expected = x % 3 == 0 ? x * 3 + 1 :
      (x % 3 == 1 ? x * 5 + 18 : (x * 5 + 7) ^ UINT64_C(0x55));
    if (results[i] != expected) {
      fprintf(stderr, "fixture: result mismatch for job %" PRIu64 "\n", i);
      return 3;
    }
  }
  uint64_t dropped = main_trace.dropped + worker_trace.dropped;
  bool complete = dropped == 0 && !main_trace.clock_error && !worker_trace.clock_error;
  printf("{\"schema_version\":1,\"run_id\":"); json_string(run_id);
  printf(",\"build_id\":"); json_string(BUILD_ID);
  printf(",\"plan_id\":"); json_string(plan_id);
  printf(",\"enabled_sites\":[");
  bool first = true;
  for (int i = S_WORK_BEGIN; i <= S_RETRY_FAIL; ++i) {
    if (!enabled[i]) continue;
    if (!first) putchar(',');
    first = false;
    json_string(site_names[i]);
  }
  printf("],\"clock\":\"CLOCK_MONOTONIC\",\"complete\":%s,\"dropped\":%" PRIu64
         ",\"serialization\":\"timestamp merge preserving thread order; seq is presentation, not happens-before\",\"events\":[",
         complete ? "true" : "false", dropped);
  size_t sequence = 0;
  size_t main_index = 0, worker_index = 0;
  while (main_index < main_trace.count || worker_index < worker_trace.count) {
    bool select_main = worker_index == worker_trace.count ||
      (main_index < main_trace.count &&
       main_trace.events[main_index].timestamp_ns <= worker_trace.events[worker_index].timestamp_ns);
    if (select_main) emit_event(&main_trace.events[main_index++], "main", &sequence);
    else emit_event(&worker_trace.events[worker_index++], "worker", &sequence);
  }
  printf("],\"results\":[");
  for (uint64_t i = 0; i < jobs; ++i) {
    if (i) putchar(',');
    printf("{\"job\":%" PRIu64 ",\"input\":%" PRIu64 ",\"result\":%" PRIu64 "}",
           i, seed + i, results[i]);
  }
  printf("]}\n");
  return ferror(stdout) ? 4 : 0;
}
