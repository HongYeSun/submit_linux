# Assignment 5 — Level 1 보고서

## 1. 범위와 실행 환경

`bt/frametrace.bt`의 TODO(L1-5, L1-6)를 구현했다. 제공된 `src/`와 Makefile은 수정하지 않았다.

- 확인한 환경: WSL2 Ubuntu, 커널 `6.18.54.1-microsoft-standard-WSL2`, vCPU 2개.
- bpftrace 0.25.0, GCC 15.2.0. `.wslconfig`: `processors=2`, `swap=0`.
- 환경 버전은 앞서 같은 WSL에서 확인한 값이다. 각 실행 당시 uname/버전을 저장한 로그는 없다.
- 완료: 추적 구현, 세 모드 각 3회 실행, 동일 실행의 frame-id join과 오차 분석.

## 2. 빌드·실행과 파일 생성 관계

```bash
make
mkdir -p logs
for r in 1 2 3; do
  for g in none signal budget; do
    sudo bpftrace bt/frametrace.bt \
      -c "./bin/frameloop -g $g -n 300 -o logs/r$r-$g-bt.csv" \
      > "logs/r$r-$g-bt.stdout.txt"
  done
done
```

|파일|실제로 생성한 프로그램·옵션|내용|
|---|---|---|
|`logs/rN-MODE-bt.csv`|`bin/frameloop ... -o 경로`|프레임별 wall/CPU 시간, 목표·완료 작업량, 상태, 신호, miss|
|`logs/rN-MODE-bt.stdout.txt`|`bpftrace bt/frametrace.bt -c ...`의 stdout 리다이렉션|프로그램 PID·설정·요약, 프레임별 F 행, 추적 map|

N은 1/2/3, MODE는 none/signal/budget이다.
총 9회·2,700프레임에서 각 CSV와 F 행의 id 0–299는 누락·중복 없이 일치했다.
각 id의 상태도 같으며 CSV의 miss/DROP/CUT/사용자 신호 합계는 프로그램 요약과 일치했다.

## 3. 구현과 측정 항목

|항목|측정 방법·의미|
|---|---|
|`wall_us`|`frame_begin`부터 `frame_end`까지의 경과 시간|
|`oncpu_us`|`sched_switch`로 닫은 실행 구간과 마지막 구간을 합산|
|`offcpu_runnable_us`|`prev_state & 0xff == 0`일 때 CPU를 떠나 다시 받을 때까지의 시간|
|`n_out`|프레임 안에서 CPU를 떠난 전체 횟수; 제공 스켈레톤 기준|
|`sigxcpu`, `sig_at_us`|`signal_generate`에서 SIGXCPU(24)의 생성 수와 최초 생성 시점|
|`status`|프로그램이 전달한 0=OK, 1=DROP, 2=CUT|

현재 WSL에서 bpftrace `tid`와 커널 이벤트 PID의 namespace가 달라 비교에는 `curtask->pid`를 쓴다.
`prev_state`의 하위 8비트만 검사해 선점 표시 0/0x100 차이를 처리한다.

고정값은 period/deadline 16667μs, runtime 4000μs, 일반 작업 2500μs, spike 6000μs
(`id % 10 == 9`), chunk 100μs, budget 3500μs다. 모든 실행은 기본 `-p dl`이며 부하는 추가하지 않았다.
실행 전 예측을 남긴 로그는 없다. 다음은 사후 해석에 사용한 기대 동작이다:
none은 초과 후 보충을 기다리고, signal은 이미 발생한 초과 통지 뒤 중단하며,
budget은 남은 작업을 포기해 예약량 소진을 피한다.

## 4. 이번 실행의 측정 결과

### 4.1 모드별 프로그램 결과

모든 행은 `-p dl -n 300`이고 파일명의 MODE가 `-g` 인자다. 시간 단위는 μs다.
원본 stdout의 요약 출력에서 추출했다. 각 CSV는 같은 파일명에서 `.stdout.txt`를 `.csv`로 바꾼 경로다.

|원본 stdout|PID|miss|periods_used|work_done %|DROP|CUT|wall p50|wall p99|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|[r1-budget-bt.stdout.txt](logs/r1-budget-bt.stdout.txt)|1048|0|300.0|90.5|0|30|2643|3513|
|[r1-none-bt.stdout.txt](logs/r1-none-bt.stdout.txt)|1020|30|330.0|100.0|0|0|2636|19011|
|[r1-signal-bt.stdout.txt](logs/r1-signal-bt.stdout.txt)|1036|30|330.0|97.4|25|0|2627|18920|
|[r2-budget-bt.stdout.txt](logs/r2-budget-bt.stdout.txt)|1084|0|300.0|90.5|0|30|2645|3533|
|[r2-none-bt.stdout.txt](logs/r2-none-bt.stdout.txt)|1060|30|330.0|100.0|0|0|2642|19122|
|[r2-signal-bt.stdout.txt](logs/r2-signal-bt.stdout.txt)|1072|30|330.0|97.0|27|0|2624|18945|
|[r3-budget-bt.stdout.txt](logs/r3-budget-bt.stdout.txt)|1120|0|300.0|90.5|0|30|2643|3540|
|[r3-none-bt.stdout.txt](logs/r3-none-bt.stdout.txt)|1096|30|330.0|100.0|0|0|2628|18944|
|[r3-signal-bt.stdout.txt](logs/r3-signal-bt.stdout.txt)|1108|30|330.0|97.3|26|0|2631|18946|

### 4.2 커널 이벤트 측정

각 행은 §4.1과 같은 stdout의 F 행에서 계산했다. 시간 단위는 μs다.
off median은 대기가 있는 프레임, sig_at median은 신호가 생성된 프레임만 집계한다.
program SIG는 CSV/요약의 사용자 핸들러 집계, trace SIG는 활성 프레임 안의 커널 생성 집계다.

|원본 stdout|on-CPU median|off 프레임|off median|program SIG|trace SIG|sig_at median|일반 DROP|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|[r1-budget-bt.stdout.txt](logs/r1-budget-bt.stdout.txt)|2601.5|0|0.0|0|0|—|0|
|[r1-none-bt.stdout.txt](logs/r1-none-bt.stdout.txt)|2588.5|30|12668.0|29|25|17294|0|
|[r1-signal-bt.stdout.txt](logs/r1-signal-bt.stdout.txt)|2584.5|30|12641.0|28|27|17285|7|
|[r2-budget-bt.stdout.txt](logs/r2-budget-bt.stdout.txt)|2602.0|0|0.0|0|0|—|0|
|[r2-none-bt.stdout.txt](logs/r2-none-bt.stdout.txt)|2594.0|30|12667.5|30|26|17307.5|0|
|[r2-signal-bt.stdout.txt](logs/r2-signal-bt.stdout.txt)|2583.0|30|12650.0|30|30|17119.5|10|
|[r3-budget-bt.stdout.txt](logs/r3-budget-bt.stdout.txt)|2601.0|0|0.0|0|0|—|0|
|[r3-none-bt.stdout.txt](logs/r3-none-bt.stdout.txt)|2585.0|31|12662.0|29|29|17395|0|
|[r3-signal-bt.stdout.txt](logs/r3-signal-bt.stdout.txt)|2586.0|30|12656.5|30|28|17373.5|9|

### 4.3 동일 실행의 CSV·추적값 대조

각 CSV의 id를 같은 이름의 stdout F 행 id에 join했다. 계산식은 다음과 같다.

```text
wall 차이 = F.wall_us − CSV.wall_us
CPU 차이  = F.oncpu_us − CSV.cpu_us
초과 건수 = abs(차이) > 200μs인 프레임 수
```

CPU 차이의 p50/p99는 제공 프로그램처럼 정렬 후 `floor(n*p/100)` 인덱스를 사용했다.
원본 CSV는 아래 링크, 해당 추적 stdout은 §4.1에 있다. 별도 join 파일 없이 원본을 읽어 계산했다.

|원본 CSV|wall 오차 >200|CPU 오차 >200|최대 abs(wall 차이)|CPU 차이 min|p50|p99|max|
|---|---:|---:|---:|---:|---:|---:|---:|
|[r1-budget-bt.csv](logs/r1-budget-bt.csv)|0|0|80.4|-83.2|-43.0|-33.2|-32.4|
|[r1-none-bt.csv](logs/r1-none-bt.csv)|0|0|131.8|-136.4|-45.5|-25.7|-23.5|
|[r1-signal-bt.csv](logs/r1-signal-bt.csv)|0|0|126.6|-132.8|-42.3|-22.3|-13.7|
|[r2-budget-bt.csv](logs/r2-budget-bt.csv)|0|0|137.6|-142.2|-43.1|-34.2|-32.7|
|[r2-none-bt.csv](logs/r2-none-bt.csv)|0|0|178.2|-181.2|-46.1|-28.4|-26.0|
|[r2-signal-bt.csv](logs/r2-signal-bt.csv)|0|0|74.2|-77.3|-41.9|-24.1|-14.5|
|[r3-budget-bt.csv](logs/r3-budget-bt.csv)|0|0|79.5|-84.7|-41.4|-33.4|-24.2|
|[r3-none-bt.csv](logs/r3-none-bt.csv)|0|0|83.7|-88.1|-42.1|-34.2|-33.6|
|[r3-signal-bt.csv](logs/r3-signal-bt.csv)|0|0|91.0|-94.7|-46.0|-26.5|-22.7|

2,700프레임 전체의 CPU 차이 분포다. 단위는 μs이며 구간은 하한 포함·상한 제외다.

|CPU 차이 구간|프레임 수|
|---|---:|
|[-inf, -200)|0|
|[-200, -100)|6|
|[-100, -50)|730|
|[-50, 0)|1964|
|[0, 50)|0|
|[50, 100)|0|
|[100, 200)|0|
|[200, inf)|0|

## 5. 필수 질문 답변 — PDF §5.3

### Q1. Compare miss, periods_used, and work_done for none, signal, and budget. In none, show with oncpu_us and offcpu_runnable_us how many periods one spike frame uses, and explain it with how CBS treats a task that has used up its runtime (throttle and replenish).

none은 세 회 모두 miss=30, periods_used=330, work_done=100%였다.
signal도 세 회 모두 miss=30, periods_used=330이었지만 완료율은 97.4/97.0/97.3%로 낮았다.
budget은 세 회 모두 miss=0, periods_used=300, 완료율=90.5%, CUT=30이었다.
출처는 §4.1의 각 원본 stdout과 같은 이름의 CSV다. budget은 작업 일부를 포기하는 대신
관측된 deadline miss와 runnable 대기를 피했고, signal은 작업을 포기해도 miss를 줄이지 못했다.

`r1-none-bt.stdout.txt` frame 9는 wall=18835μs, on-CPU=6199μs,
runnable off-CPU=12635μs다. 약 6.2ms의 CPU 작업에 약 12.6ms의 대기가 더해져
16.667ms 주기를 넘었다. 같은 실행의 CSV frame 9도 miss=1이다.
전체 300프레임에 330주기가 필요해 30개 spike가 각각 추가 예약 주기를 소비한 결과와 부합한다.

CBS는 runtime을 다 쓴 태스크의 실행을 제한하고 보충 후 나머지 작업을 진행시킨다.
그 비용을 이 실험에서는 프레임 지연과 추가 주기가 부담한다.
[`frameloop.c:93–98`](src/frameloop.c#L93)은 reservation을 설정하고
[162행](src/frameloop.c#L162)은 프레임 종료 후 `sched_yield()`한다.
CBS 내부 잔여 budget을 직접 읽은 것은 아니므로 runnable 대기 전부를 무조건 throttling이라
단정하지 않는다. 예를 들어 r3-none에는 대기 프레임이 31개로 spike 수 30개보다 하나 많다.

### Q2. Did signal mode reduce misses? Use sig_at_us to show when the kernel generated SIGXCPU. If frames that were not spikes were dropped, explain why. Can SIGXCPU be used to detect an overrun, or to prevent one? Answer with evidence, citing the KEY lines of the DROP and CUT decisions.

signal은 이번 세 회에서 모두 miss=30으로 none보다 줄지 않았다.
`r1-signal-bt.stdout.txt` frame 9는 sig_at=16729μs, wall=16787μs,
on-CPU=4123μs, off-CPU=12662μs이며 DROP이다.
SIGXCPU가 생성될 때 이미 주기 16667μs를 넘었으므로 이후 중단으로 miss를 되돌릴 수 없다.
일반 프레임 DROP은 회차별 7/10/9개였다(§4.2).

`r3-signal-bt.stdout.txt` frame 9는 18795μs를 쓰고 OK로 끝났지만 그 프레임 안의
신호 생성은 0이다. 다음 일반 frame 10에는 sig_at=2133μs에 신호가 생성되고
wall=2167μs에 DROP됐다. SIGXCPU는 프레임 id를 전달하지 않으므로 초과 통지가 늦게
생성·전달되면 일반 프레임에서도 플래그를 보고 중단할 수 있다.

[36행의 핸들러](src/frameloop.c#L36)는 플래그만 설정하고
[94행](src/frameloop.c#L94)은 `SCHED_FLAG_DL_OVERRUN`을 요청한다.
[143–145행의 DROP](src/frameloop.c#L143)은 플래그를 본 다음 중단하는 반면,
[147–150행의 CUT](src/frameloop.c#L147)은 다음 chunk까지의 CPU 사용량을 사전에 검사한다.
따라서 SIGXCPU는 초과의 사후 감지에 쓰며 단독으로 예방을 보장하지 못한다.
budget은 여유 500μs를 두고 작업을 줄였고 이번 데이터에서는 세 회 모두 대기·신호·miss가 0이었다.
다른 부하나 관측 비용에서도 항상 0이라고 보장하는 결과는 아니다.

신호 수의 불일치도 보존했다. 예를 들어 r1-none의 사용자 핸들러 집계는 29이고 프레임 안
커널 생성 집계는 25다. 전자는 CSV/프로그램 경계의 전달 결과, 후자는 uprobe 활성 구간의 생성 이벤트다.

### Q3. Show the distribution of the difference between frametrace oncpu_us and the program's cpu_us (CLOCK_THREAD_CPUTIME_ID). What should you suspect for frames with a large difference? Measure the observation cost as the change in frame_wall p50 with and without bpftrace, and explain where it comes from.

§4.3의 같은 실행 join 결과, 2,700프레임 모두에서 wall/CPU 오차의 절댓값이 200μs를
넘는 프레임은 **각각 0개**였다. 따라서 이번 데이터에는 임계값을 넘는 프레임의 공통점을
분석할 대상이 없다. 임계값 초과가 없다는 결과가 두 측정값이 완전히 같다는 뜻은 아니다.
CPU 차이는 모두 음수이며 회차별 p50은 약 -41.4~-46.1μs다.

가장 큰 CPU 차이는 `r2-none-bt.csv`와 같은 이름 stdout의 일반 frame 34다.
프로그램 CPU=2909.2μs, 추적 on-CPU=2728μs로 차이는 -181.2μs이며,
이 역시 200μs 기준 안이다. 해당 프레임은 CPU 이탈·신호·miss가 모두 0이었다.
전체 CPU 차이의 p50=-43.4μs, p99=-25.9μs, 최대값=-13.7μs다.
프로그램은 [133행](src/frameloop.c#L133)에서 thread CPU 측정을 시작해
[156행](src/frameloop.c#L156)에서 끝낸다. uprobe는 그 안의
[135행](src/frameloop.c#L135)~[154행](src/frameloop.c#L154)을 측정하므로,
경계 밖의 처리와 probe 진입·복귀 비용, 커널 CPU 회계 차이가 음의 오프셋을 설명할 수 있다.
더 큰 차이가 나타나면 선점·신호 처리·이벤트 누락·가상화 및 측정 경계를 확인해야 한다.

### Q4. Under background load, compare miss and frame_wall p99 for dl, fifo:50, and other. How did FIFO handle the spike frames, and who paid for it? Why are non-spike frames late under other? For a webOS frame pacing thread, which policy would you choose, and on what basis would you set its runtime?

다음은 정책 원리에 따른 예상이다. FIFO는 일반 작업보다 우선해 spike를 마칠 수 있지만
그 CPU의 다른 작업이 기다리는 비용을 부담한다. OTHER는 burn 작업과 CPU 시간을 나누므로
일반 프레임도 늦어질 수 있다. DL은 예약량을 제공하면서 초과 작업을 제한한다.
실제 miss와 p99 비교는 PDF §5.2의 부하 실험이 필요하다.

프레임 일부를 포기할 수 있고 다른 작업의 CPU도 보호해야 하는 webOS pacing에는 DL과
사전 budget 제어를 우선 검토한다. 이번 budget의 90.5% 완료율·miss=0은 그 교환 관계의
참고 근거지만 부하 상태의 최적 정책을 입증하지는 않는다.
runtime은 실제 CPU 작업량 분포, 필요한 완료율, 관측 비용·안전 여유, 다른 예약의 admission
가능성으로 정한다. 6ms spike를 반드시 끝내려면 4ms 예약만으로 부족하므로 작업량을 줄이거나
예약량을 재설계해야 한다.

## 6. AI 사용 명세

AI를 추적 구현과 로그 대조·README 작성에 활용했다. 최초 구현의 PID namespace 가정을
실제 이벤트와 대조해 거절하고 `curtask->pid`로 수정했다.
이번 표는 사용자의 최신 CSV/stdout을 직접 읽어 계산했다.
