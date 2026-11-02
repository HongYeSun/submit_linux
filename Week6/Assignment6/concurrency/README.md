# Assignment 6 — Level 1 보고서

## 1. 범위와 실행 환경

`src/bq.c`의 TODO(L1-1~4), 즉 `bq_put`, `bq_get`, `bq_timed_get`, `bq_close`를 구현했다.
제공된 헤더, 테스트, Makefile, tools와 Level 2/3 소스는 수정하지 않았다. 아래 결과는 `logs/`의 실제 출력만 사용한다.

- 실행: WSL2 Ubuntu, vCPU 2개.
- 커널: `6.18.54.1-microsoft-standard-WSL2` — 명세 지정 6.6과 다르다.
- glibc 2.43, bpftrace 0.25.0, GCC 15.2.0.
- 환경·빌드 명령·검사 요약: [l1-check.txt](logs/l1-check.txt).
- 일반 빌드: `-O2 -g -Wall -Wextra -std=gnu11 -fno-omit-frame-pointer -pthread -rdynamic`.
- TSan 빌드: 제공 Makefile의 `-O1 -g -std=gnu11 -fsanitize=thread -pthread`.
- 구현과 일반·TSan 검사는 완료했다. 제공 `futex.bt`가 구문 오류로 실행되지 않아 op/caller별 측정값은 없다. Level 2/3은 미구현이다.
- WSL root로 실행했다. `vm.mmap_rnd_bits=32`에서도 이번 TSan 검사는 통과하여 sysctl은 변경하지 않았다.

## 2. 빌드·실행과 파일 생성 관계

WSL에서 `Week6/Assignment6/concurrency`로 이동한 후 실행했다.
전체 `make` 대신 Level 1에 필요한 제공 타깃만 빌드했다.

```bash
mkdir -p logs
{
  date -Is
  uname -r
  nproc
  getconf GNU_LIBC_VERSION
  bpftrace --version
  gcc --version | head -1
  echo 'COMMAND: make bin/bqtest bin/bqtest_tsan bugif'
  make bin/bqtest bin/bqtest_tsan bugif
  echo 'COMMAND: bash tools/l1_check.sh'
  bash tools/l1_check.sh
} 2>&1 | tee logs/l1-check.txt
```

다음 명령을 각 3회 실행했다. `r`은 회차이며, 각 로그 앞에는 `date -Is`, `uname -r`,
실행 전 예측과 `COMMAND:`를 함께 기록했다. 실제 인자는 각 원본 로그에서 확인할 수 있다.

```bash
for r in 1 2 3; do
  for q in 1 4 64; do
    ./bin/bqtest -p 2 -c 2 -n 100000 -q "$q" 2>&1 | tee "logs/r$r-l1-q$q.txt"
  done
  ./bin/bqtest -p 4 -c 4 -n 50000 -q 4    2>&1 | tee "logs/r$r-l1-signal.txt"
  ./bin/bqtest -p 4 -c 4 -n 50000 -q 4 -b 2>&1 | tee "logs/r$r-l1-broadcast.txt"
  ./bin/bqtest -p 2 -c 2 -n 50000 -q 4 -w 20 2>&1 | tee "logs/r$r-l1-work.txt"
  { make bugif && ./bin/bqtest_bugif -p 2 -c 2 -n 20000 -q 4 -T 10; } \
    2>&1 | tee "logs/r$r-l1-bugif.txt"
  for q in 1 64; do
    bpftrace tools/futex.bt -c "./bin/bqtest -p 2 -c 2 -n 20000 -q $q" \
      > "logs/r$r-l1-futex-q$q.txt" 2>&1
  done
done
```

일반 사용자로 futex 명령을 재실행할 때는 명세대로 `sudo bpftrace`를 사용한다.
위 코드 블록은 프로그램 실행·저장 부분이며, 로그에 기록한 예측은 §3과 같다.

|로그|생성한 프로그램·명령|내용|
|---|---|---|
|`logs/l1-check.txt`|환경 명령, make, `bash tools/l1_check.sh`|버전·빌드 출력·8개 검사 요약|
|`logs/l1-bqtest-{close,timeout,stress,stress-b}.txt`|`l1_check.sh`가 일반 바이너리 실행|각 검사의 원본 출력|
|`logs/l1-bqtest_tsan-{close,timeout,stress,stress-b}.txt`|`l1_check.sh`가 TSan 바이너리 실행|검사 결과와 TSan 진단 출력|
|`logs/rN-l1-q{1,4,64}.txt`|일반 bqtest, `-p 2 -c 2 -n 100000`|용량 비교, PID·처리량·대기 횟수·오류 수|
|`logs/rN-l1-{signal,broadcast}.txt`|일반 bqtest, `-p 4 -c 4 -n 50000 -q 4`, broadcast만 `-b`|깨우기 방식 비교|
|`logs/rN-l1-work.txt`|일반 bqtest, `-p 2 -c 2 -n 50000 -q 4 -w 20`|작업 완료 순서 역전|
|`logs/rN-l1-bugif.txt`|`make bugif` 및 `bin/bqtest_bugif ... -T 10`|의도적으로 잘못된 if 빌드의 실패|
|`logs/rN-l1-futex-q{1,64}.txt`|제공 `tools/futex.bt`|실행 시각·명령과 구문 오류 원문; 측정 map은 없음|

N은 1/2/3이다. 총 36개 원본 로그다.  
일반·BUG_IF 부하 출력에는 프로그램 PID가 있다. close/timeout의 제공 출력은 PID를 출력하지 않으며,
futex는 구문 분석 단계에서 실패하여 대상 프로그램 PID가 없다.

## 3. 구현과 실행 전 예측

|구현|동작|
|---|---|
|`bq_put`|mutex 아래에서 full이고 열려 있으면 `BQ_WHILE`로 대기하고 횟수를 센다. 닫혔으면 삽입하지 않는다. 삽입 후 소비자를 깨운다.|
|`bq_get`|empty이고 열려 있으면 대기한다. close 이후에도 남은 항목을 먼저 반환하고 생산자를 깨운다. closed이면서 empty일 때만 `BQ_CLOSED`다.|
|`bq_timed_get`|lock을 잡기 전에 MONOTONIC deadline을 한 번 계산한다. timed wait의 `ETIMEDOUT` 후에도 mutex 아래에서 항목 → closed → timeout 순서로 판정한다. 0ms는 대기하지 않는다.|
|`bq_close`|mutex 아래에서 closed를 설정하고 양쪽 조건변수에 broadcast한다. 이미 닫혔다면 추가 작업을 하지 않는다.|

대기 횟수는 조건변수 대기 호출 직전에 증가시킨다. 나머지 공유 필드와 함께 같은 mutex로 보호한다.
삽입·인출의 깨우기는 제공 `wake()`를 사용하여 기본 signal과 `-b`의 broadcast를 비교한다.

## 4. 이번 실행의 측정 결과

### 4.1 제공 검사

[l1-check.txt](logs/l1-check.txt)의 최종 출력은 `l1_check: all PASS`다.
일반·TSan 각각 close, timeout, stress, stress-b 모두 PASS이며 TSan 보고는 없었다.
TSan 실행 시간은 성능 비교에 사용하지 않았다.

|검사|일반 원본|TSan 원본|결과|
|---|---|---|---|
|close|[일반](logs/l1-bqtest-close.txt)|[TSan](logs/l1-bqtest_tsan-close.txt)|모두 PASS|
|timeout|[일반](logs/l1-bqtest-timeout.txt)|[TSan](logs/l1-bqtest_tsan-timeout.txt)|모두 PASS|
|stress|[일반](logs/l1-bqtest-stress.txt)|[TSan](logs/l1-bqtest_tsan-stress.txt)|모두 PASS|
|stress-b|[일반](logs/l1-bqtest-stress-b.txt)|[TSan](logs/l1-bqtest_tsan-stress-b.txt)|모두 PASS|

일반 timeout 검사의 실제 출력이다.

```text
timeout tests (CLOCK_MONOTONIC)
  timeout_50ms           ok   empty queue, 50 ms: BQ_TIMEOUT after 50..150 ms (got 50.1 ms)
  timeout_0              ok   timeout 0: BQ_TIMEOUT without blocking (got 0.0 ms)
  item_arrives           ok   item put after 20 ms: BQ_OK well before 200 ms (got 20.2 ms)
  deadline_fixed         ok   wakeups every 10 ms, no item: BQ_TIMEOUT at 300..450 ms (got 300.1 ms)
  close_arrives          ok   closed after 20 ms: BQ_CLOSED well before 200 ms (got 20.2 ms)
  item_and_closed        ok   an available item wins over closed
  get_wakes_producer     ok   producer entered the full-queue wait path
  get_wakes_producer     ok   bq_timed_get took an item and the blocked producer returned (0.0 ms)
PASS
```

### 4.2 용량·깨우기·작업량 비교

모두 일반 빌드다. q1/q4/q64는 p=2, c=2, n=100000, signal, work=0이다.
signal/broadcast는 p=4, c=4, n=50000, q=4, work=0이며 broadcast만 -b를 쓴다.
work는 p=2, c=2, n=50000, q=4, signal, work=20μs다. n은 생산자당 항목 수다.
각 원본에 정확한 명령이 있다. 모든 행에서 lost/duplicate/garbage/order_violation/early_closed는 0이고 PASS다.

|원본 로그·설정|PID|items/s|put waits|get waits|elapsed ms|completion inversions|
|---|---:|---:|---:|---:|---:|---:|
|[r1-l1-q1.txt](logs/r1-l1-q1.txt)|1640|20313|198670|198572|9845.8|98|
|[r2-l1-q1.txt](logs/r2-l1-q1.txt)|1720|20678|197210|197291|9671.9|195|
|[r3-l1-q1.txt](logs/r3-l1-q1.txt)|1800|20468|198513|198539|9771.5|104|
|[r1-l1-q4.txt](logs/r1-l1-q4.txt)|1649|413542|54952|52931|483.6|1956|
|[r2-l1-q4.txt](logs/r2-l1-q4.txt)|1729|406983|52285|55417|491.4|1537|
|[r3-l1-q4.txt](logs/r3-l1-q4.txt)|1809|426271|53518|53000|469.2|1753|
|[r1-l1-q64.txt](logs/r1-l1-q64.txt)|1658|3995014|4083|1592|50.1|178|
|[r2-l1-q64.txt](logs/r2-l1-q64.txt)|1738|2233048|5251|4666|89.6|3412|
|[r3-l1-q64.txt](logs/r3-l1-q64.txt)|1818|4148697|4118|1479|48.2|150|
|[r1-l1-signal.txt](logs/r1-l1-signal.txt)|1667|599126|45079|43724|333.8|1695|
|[r2-l1-signal.txt](logs/r2-l1-signal.txt)|1747|666615|40819|43457|300.0|591|
|[r3-l1-signal.txt](logs/r3-l1-signal.txt)|1827|603583|43610|43628|331.4|1103|
|[r1-l1-broadcast.txt](logs/r1-l1-broadcast.txt)|1680|497669|51989|49076|401.9|4622|
|[r2-l1-broadcast.txt](logs/r2-l1-broadcast.txt)|1760|613497|45771|42087|326.0|995|
|[r3-l1-broadcast.txt](logs/r3-l1-broadcast.txt)|1840|501506|51104|48538|398.8|4257|
|[r1-l1-work.txt](logs/r1-l1-work.txt)|1693|60035|96940|630|1665.7|1909|
|[r2-l1-work.txt](logs/r2-l1-work.txt)|1773|62534|96818|726|1599.1|1905|
|[r3-l1-work.txt](logs/r3-l1-work.txt)|1853|55233|96569|612|1810.5|1899|

3회 중앙값이다. 원본은 위 표의 같은 설정 3개 로그다.

|설정|items/s 중앙값|put waits 중앙값|get waits 중앙값|
|---|---:|---:|---:|
|q1|20468|198513|198539|
|q4|413542|53518|53000|
|q64|3995014|4118|1592|
|signal|603583|43610|43628|
|broadcast|501506|51104|48538|
|work|60035|96818|630|

용량을 1에서 4, 64로 늘리자 full/empty 대기 횟수가 크게 감소하고 처리량이 증가했다.
broadcast는 이번 세 번 모두 signal보다 처리량이 낮았다. 다만 2회차 get waits는 broadcast가 더 적으므로
모든 종류의 대기가 항상 증가한다고 단정하지 않는다. 용량 64에서도 실행 간 변동이 있다.

### 4.3 BUG_IF와 올바른 빌드

BUG_IF는 p=2, c=2, n=20000, q=4, signal, -T 10이다.
올바른 빌드의 q4 행은 n=100000이므로 동일 부하의 속도 비교로 취급하지 않는다.
BUG_IF는 데이터를 손상시키므로 높은 items/s에 성능상의 의미를 부여하지 않는다.

|원본 로그|PID|lost|duplicate|order_violation|early_closed|결과|
|---|---:|---:|---:|---:|---:|---|
|[r1-l1-bugif.txt](logs/r1-l1-bugif.txt)|1703|33311|33311|8856|1|FAIL|
|[r2-l1-bugif.txt](logs/r2-l1-bugif.txt)|1783|36421|36421|9377|1|FAIL|
|[r3-l1-bugif.txt](logs/r3-l1-bugif.txt)|1863|37407|37407|9492|1|FAIL|

이번 세 번은 hang 없이 위 오류를 출력하고 종료했다.

### 4.4 futex 측정 실패

|설정|원본 로그|결과|
|---|---|---|
|p=2, c=2, n=20000, q=1|[r1](logs/r1-l1-futex-q1.txt), [r2](logs/r2-l1-futex-q1.txt), [r3](logs/r3-l1-futex-q1.txt)|모두 구문 분석 실패, 측정값 없음|
|p=2, c=2, n=20000, q=64|[r1](logs/r1-l1-futex-q64.txt), [r2](logs/r2-l1-futex-q64.txt), [r3](logs/r3-l1-futex-q64.txt)|모두 구문 분석 실패, 측정값 없음|

6회 모두 대상 프로그램 실행 전 다음 오류로 종료했다.

```text
tools/futex.bt:36:1-2: ERROR: syntax error, unexpected }
}
~
```

큐의 검사 실패가 아니라 제공 트레이서의 오류다. 수정 금지인 tools는 변경하지 않았다.
op/caller별 호출 수·시간은 얻지 못했으며, 큐 대기 횟수로 대체하거나 추정하지 않았다.

## 5. 필수 질문 답변 — PDF §5.3

### Q1. Show which failures the BUG_IF build produced (lost, duplicate, order_violation, early_closed, or a hang). Explain one of them with an interleaving of two threads, and why the same code passes in the correct build.

§4.3의 세 로그 모두 lost, duplicate, order_violation, early_closed가 0보다 컸다.
r1은 각각 33311, 33311, 8856, 1이다. 다음은 재검사의 필요성을 설명하는 실행 순서 예시이며,
이번 실행의 스레드별 추적 결과는 아니다.

1. 빈 큐에서 소비자 A가 기다린다.
2. 생산자 B가 항목을 넣고 A에게 signal한다. signal은 mutex나 항목을 A에게 예약하지 않는다.
3. A가 mutex를 다시 잡기 전에 다른 소비자가 항목을 꺼낸다.
4. A가 재개하면 다시 빈 큐다. if 빌드는 조건을 재검사하지 않고 진행한다.

이 구현의 if 버전 bq_get은 빈 큐에서 BQ_CLOSED를 반환하여 close 전 소비자가 종료할 수 있다.
생산자도 다른 생산자가 빈자리를 차지한 후 재개하면 full 상태에서 push해 용량 불변 조건을 깨뜨릴 수 있다.
이는 링버퍼 덮어쓰기나 오래된 항목 재사용, 유실·중복으로 이어질 수 있다.
while은 같은 mutex 아래에서 조건을 재검사해 조건이 맞지 않으면 다시 기다린다.
올바른 빌드는 §4.1·§4.2에서 해당 오류가 모두 0이었다.

### Q2. In the default signal mode, why must bq_close still wake every waiter? Which close test would fail if bq_close used signal, and which threads would stay asleep? Why must closed be checked under the same mutex as the wait (the lost wake-up of the lecture)?

close 이후에는 새로운 put/get 알림이 계속 발생한다고 보장할 수 없다. 한 명만 깨우면 나머지가 계속 잠들 수 있다.
[close 로그](logs/l1-bqtest-close.txt)의 consumers_wake와 producers_wake는 각각 3명이 대기 경로에 들어간 것을 확인하고 close한 뒤,
전원이 200ms 안에 BQ_CLOSED로 복귀하는지 검사한다. signal만 쓰면 각 조건변수에서 선택되지 않은 대기자가 남을 수 있다.
구현은 플래그와 관계없이 양쪽에 broadcast하며 일반·TSan 모두 통과했다.

closed 확인·변경과 wait 진입을 같은 mutex로 동기화한다.
동기화하지 않으면 열린 상태를 읽은 직후, 실제 대기 전에 close와 알림이 끝나 알림을 놓치고 잠드는 구간이 생긴다.
cond_wait는 mutex 해제와 대기 진입을 원자적으로 수행해 이 lost wake-up 구간을 막는다.

### Q3. Compare items/s and put/get waits for CAP 1, 4, and 64. With futex.bt, separate the futex calls made inside pthread_mutex_lock, pthread_mutex_unlock, pthread_cond_wait, pthread_cond_signal, and pthread_join. Which of them would remain without any lock contention? Explain why many futex calls do not by themselves mean mutex contention.

§4.2의 9개 용량 로그와 중앙값 표에서 용량 증가에 따라 처리량은 늘고 대기는 줄었다.
put/get waits는 조건변수 대기 호출 횟수이며 futex syscall 횟수가 아니다.
제공 futex.bt가 §4.4의 구문 오류로 실패해 mutex_lock/unlock, cond_wait/signal, join별 실측 분류는 미완료다.

구현 원리상 경합 없는 mutex는 사용자 공간 fast path로 완료할 수 있지만 큐가 비거나 가득 차면 조건변수 대기가 필요하다.
조건변수의 wait/wake와 아직 종료되지 않은 스레드에 대한 pthread_join도 futex를 사용할 수 있다.
따라서 mutex_lock의 WAIT, unlock의 WAKE와 cond_wait의 WAIT_BITSET 등을 호출 함수와 함께 구분해야 한다.
모든 pthread 호출이 반드시 syscall을 발생시킨다는 뜻은 아니며 glibc 상태·버전에 따라 달라진다.
이번 로그로 해당 분류를 측정했다고 주장하지 않는다.

### Q4. With -w, completion_inversions is nonzero while the dequeue order check passes. Explain the difference between dequeue order and completion order, and what a FIFO queue does and does not promise to consumers. Then explain why bq_timed_get computes its deadline once on CLOCK_MONOTONIC, and what the deadline_fixed test would show otherwise.

work 실험은 세 번 모두 order_violation=0으로 PASS했지만 completion_inversions는 1909/1905/1899였다.
A가 먼저 꺼낸 뒤 선점되고 B가 다음 항목을 꺼내 먼저 처리하면 FIFO를 지키면서 완료 순서는 역전된다.
FIFO는 꺼내는 순서를 보장하며 mutex를 놓은 뒤의 처리 완료 순서까지 보장하지 않는다.
제공 order_violation 검사는 각 소비자가 본 생산자별 sequence를 검사하며 모든 소비자의 dequeue 시각을 기록한 전역 추적은 아니다.

상대 timeout은 호출 전체에 대한 시간 제한이다. 항목 없는 wake마다 deadline을 다시 계산하면 총 대기가 연장된다.
CLOCK_MONOTONIC은 벽시계 시각 변경의 영향을 피한다.
[timeout 로그](logs/l1-bqtest-timeout.txt)의 deadline_fixed는 10ms마다 깨워도 300.1ms에 timeout하여 PASS했다.
매번 갱신한다면 300~450ms 허용 구간을 벗어나 검사가 실패할 수 있다.
0ms는 0.0ms에 반환했으며, 항목과 closed가 동시에 있을 때 항목 우선, timed get 후 생산자 깨우기도 PASS였다.

## 6. AI 사용 명세

AI를 bq.c 구현, 제공 명령 실행, 원본 로그 집계, README 작성에 사용했다.
초기 timed wait 제안에는 ETIMEDOUT 시 break가 있었지만 BQ_WHILE이 BUG_IF에서 if로 바뀌면
루프 밖 break가 되어 빌드할 수 없으므로 기각하고 반환값을 대기 조건에 포함하는 방식으로 수정했다.
수정 후 제공 Makefile로 일반·TSan·BUG_IF를 실제 빌드하여 일반·TSan 통과와 의도한 BUG_IF 실패를 확인했다.
미측정 futex 값이나 Level 2/3 결과는 작성하지 않았다.
