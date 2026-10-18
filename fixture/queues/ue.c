#include "queues.h"
int main(void) { notifiedFIFO_t fifo; initNotifiedFIFO(&fifo); pullNotifiedFIFO(&fifo); return 0; }
