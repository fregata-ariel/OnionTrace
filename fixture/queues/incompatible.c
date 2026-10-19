struct other { int fifo; };
void pushNotifiedFIFO(struct other *q, int e) { (void)q; (void)e; }
int pullNotifiedFIFO(struct other *q) { return q->fifo; }
int main(void) { struct other q = {0}; pushNotifiedFIFO(&q, 1); return pullNotifiedFIFO(&q); }
