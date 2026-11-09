# Assignment 7 — Level 1 보고서

## 1. 범위와 실행 환경

스켈레톤의 `parse_smaps()`, `classify()`, `main()`을 중심으로 정리한 `level1/vasdump.py`를 빌드해 측정했다. Idle 비교에는 직접 작성한 `level1/idle_sleep.c`를 사용했고, 할당·스레드·재귀·성장·ELF 실험에는 제공 워크로드를 사용했다. Makefile의 `vasdump` 타깃은 Windows 체크아웃에서도 Linux가 shebang을 읽을 수 있도록 줄 끝을 LF로 변환한다.

- WSL2 Ubuntu, 커널 `6.18.54.1-microsoft-standard-WSL2`, 아키텍처 `x86_64`, 페이지 크기 4,096 bytes, 스택 제한 8,192 KiB.
- glibc 2.43, GCC 15.2.0, GNU Make 4.4.1, Python 3.14.4, GNU Binutils 2.46.
- `randomize_va_space=2`. `GLIBC_TUNABLES`, `MALLOC_*`, `LD_PRELOAD`를 지정하지 않아 기본 allocator 설정을 사용했다. 빌드 옵션은 §2에 기록했다.

## 2. 빌드·실행과 파일 생성 관계

`Week7/Assignment7/virtual_memory`의 소스와 Makefile을 WSL의 임시 디렉터리에 복사해 빌드했다. 같은 디렉터리 구조에서 다음 명령으로 재현할 수 있다.

```bash
make vasdump observation_workload checkpoint_workload elf_workload
cc -D_GNU_SOURCE -O1 -g -std=c11 -Wall -Wextra -Wpedantic -fno-builtin \
   -fno-omit-frame-pointer level1/idle_sleep.c -o idle_sleep
```

`make vasdump`는 Python 소스에서 실행 가능한 `./vasdump`를 생성한다. `elf_workload`는 `-fPIE -pie`, `observation_workload`는 `-fno-optimize-sibling-calls -pthread`를 추가해 빌드한다.

| 원본 출력 | 실행 명령과 관찰 시점 |
|---|---|
| [idle.txt](samples/idle.txt) | `./vasdump -v -- sleep 1000`, `./vasdump -v -- ./idle_sleep`. 각각 기본 1초 후 덤프 |
| [allocations.txt](samples/allocations.txt) | `./checkpoint_workload`. baseline, A, B마다 `./vasdump -v PID` |
| [threads.txt](samples/threads.txt) | `./observation_workload threads`. `four_threads`에서 `ls /proc/PID/task`와 `./vasdump -v PID`. 별도 실행의 `strace`에서 스택·가드 관련 행만 발췌 |
| [stack.txt](samples/stack.txt) | `./observation_workload stack 8`과 `./observation_workload stack 64`. 각각 `deepest`에서 `./vasdump -v PID` |
| [growth.txt](samples/growth.txt) | `./vasdump --delay 1.2 --diff 5 -- ./observation_workload growth 12` |
| [elf.txt](samples/elf.txt) | `readelf -hW`, `readelf -lW`, `readelf -SW`, `nm -n`을 `./elf_workload`에 실행. 각 실행의 `layout`에서 `./vasdump -v PID`. 첫 실행에서 `cat /proc/PID/maps`도 저장 |

각 파일에 실제 명령 출력과 실행 순서를 그대로 기록했다. 설정별로 새 프로세스에서 3회 측정했다. 워크로드가 `READY`를 출력하고 Enter를 기다리는 실험은 해당 PID를 덤프한 뒤 진행했다. A는 1 KiB 블록 1,000개, B는 여기에 16 MiB 한 블록을 추가한 단계다. 제공 워크로드는 각 블록에서 페이지 간격으로 쓰고 마지막 바이트도 쓴다. 성장 실험은 같은 방식으로 1 MiB씩 쓰고 각 `growing` 표식 후 1초씩 멈춘다.

## 3. 구현과 측정 항목

- `/proc/PID/smaps`의 VMA 헤더와 8개 필드를 읽고, 주소 범위를 키로 하는 딕셔너리에 저장한다. `/proc/PID/exe`의 장치·inode로 주 실행 파일을 식별하고, inode가 없을 때 경로를 비교한다. `[heap]`, `[stack]`, 기타 대괄호 이름을 먼저 분류하고, 나머지는 주 실행 파일, `.so` 또는 `.so.*` 공유 라이브러리, 기타 파일, 이름 없는 익명 매핑으로 나눈다. 출력에는 경로의 공백과 `(deleted)`를 보존한다.
- `Size`는 가상 주소 범위 크기, `Rss`는 현재 상주한 크기, `Pss`는 공유 페이지를 공유 수에 따라 나눈 상주 크기다. `Shared_Clean`, `Shared_Dirty`, `Private_Clean`, `Private_Dirty`는 공유·전용과 수정 여부를 구분한다. `Anonymous`는 커널이 해당 VMA에 대해 보고한 익명 메모리 필드다. 익명 범주 VMA의 `Size` 합계는 별도로 읽는다. 모든 메모리 열은 1 kB = 1,024 bytes 기준이다.
- `-v`는 VMA별 주소, 권한, 파일 오프셋, Size/Rss/Pss와 경로를 출력한다. 주소와 파일 오프셋은 16진수다. 범주별 합계와 권한별 합계는 각각 전체 8개 열과 일치한다.
- `--diff`는 같은 주소 범위를 매칭한다. 추가·삭제의 반대편 값은 0으로 두고, RSS 변화량 절댓값, Size 변화량 절댓값, 시작 주소 순으로 정렬한다. 범주 및 프로세스의 signed delta를 함께 출력한다.
- PID 모드에서는 대상에 신호를 보내지 않는다. 명령 모드는 측정 후 SIGTERM을 보내고 1초 내 종료하지 않으면 SIGKILL로 전환해 회수한다. 필수 필드 누락, 읽기 실패, 측정 전 종료는 진단과 비정상 종료 코드로 구분한다.

## 4. 이번 실행의 측정 결과

### 4.1 필수 실험 5종

표의 메모리 단위는 kB다. 세 실행에서 같은 값은 하나로 표시했고, 값이 다른 경우 범위를 표시했다.

| 실험 | 3회 측정 결과 | 해석 |
|---|---|---|
| Idle | `sleep`은 VMA 48개, 전체 Size 16,112, Rss 7,664–7,832. 최소 C는 VMA 24개, 전체 Size 2,772, Rss 1,936–1,940. | 최소 C도 주 실행 파일이 5개 VMA로 나뉘고, libc와 동적 로더가 각 5개 VMA를 차지했다. 스택·힙과 `[vvar]`·`[vdso]`도 있었다. 이 환경의 `sleep`은 `/usr/lib/cargo/bin/coreutils/sleep`이며, 주 실행 파일의 Size만 11,088 kB이고 `libpcre2`, `libm` 등도 로드했다. |
| 소형·대형 할당 | `[heap]` Size/Rss는 baseline `0/0` → A `1,056/1,016` → B `1,056/1,016`. 익명 범주 Size/Rss는 `76/36` → `76/36` → `16,464/16,424`. | A에서 소형 블록은 힙에 나타났다. B에서는 Size/Rss가 각각 16,388 kB인 익명 `rw-p` VMA가 추가됐다. 16 MiB 요청보다 4 kB 큰 매핑은 allocator 메타데이터와 페이지 정렬이 개입한 결과로 해석했다. |
| 스레드 | 메인 포함 TID 5개. 전체 Size 35,552, Rss 1,976–1,980. 큰 익명 `rw-p` VMA 하나의 Size/Rss는 `32,784/32`. | 워커는 명시적으로 `malloc`을 호출하지 않는다. 큰 VMA의 Size는 `4 × (8,192 + 4)` kB다. 별도 `strace`에서 연속된 `MAP_STACK` 매핑 4개와 각 첫 페이지에 대한 `MADV_GUARD_INSTALL` 성공을 확인했다. 가드는 VMA 내부에 설치돼 `---p` 행으로 분리되지 않았다. |
| 스택 깊이 | 깊이 인자 8에서 `[stack]` Size/Rss `132/48–52`, 64에서 `272–276/272–276`. | 프레임마다 4 KiB 배열을 쓰고 최심점에서 측정했다. 같은 회차의 깊이 8과 64를 비교하면 `[stack]`의 Rss는 224–228 kB 늘었고, 주소 예약도 확대됐다. |
| 시간에 따른 증가 | 5초간 익명 범주의 Size/Rss/Pss가 각각 `+5,140`. 전체 Size/Rss도 각각 `+5,140`. | `1 MiB`를 할당하고 쓰는 단계 5개분에 페이지 단위 오버헤드가 더해진 변화와 일치한다. 1회차에는 같은 끝 주소를 가진 `2,056` kB 범위가 삭제되고 `7,196` kB 범위가 추가됐다. 순증가량은 `+5,140`이다. 3회차에는 libc의 Pss도 3 kB 증가했다. |

### 4.2 ELF 세그먼트와 VMA 매칭

[elf.txt](samples/elf.txt)의 1회차에서 `initialized_global`은 `0x5705c2c5f014`, `second_global`은 `0x5705c2c5f010`이다. `nm -n`의 심볼 값 `0x4014`, `0x4010`을 각각 빼면 두 경우 모두 load bias `B = 0x5705c2c5b000`이다. 표의 주소와 크기는 16진수다.

| `PT_LOAD` | Offset / VirtAddr / FileSiz / MemSiz | 플래그 | 런타임 세그먼트 시작 | 매칭 VMA |
|---|---|---|---|---|
| 1 | `0x0000 / 0x0000 / 0x09c0 / 0x09c0` | R | `B+0x0000` | `B+0x0000`–`B+0x1000` `r--p`, 파일 오프셋 `0x0000` |
| 2 | `0x1000 / 0x1000 / 0x04b1 / 0x04b1` | R E | `B+0x1000` | `B+0x1000`–`B+0x2000` `r-xp`, 파일 오프셋 `0x1000` |
| 3 | `0x2000 / 0x2000 / 0x0230 / 0x0230` | R | `B+0x2000` | `B+0x2000`–`B+0x3000` `r--p`, 파일 오프셋 `0x2000` |
| 4 | `0x2d58 / 0x3d58 / 0x02c0 / 0x800308` | RW | `B+0x3d58` | `B+0x3000`–`B+0x4000` `r--p`, 오프셋 `0x2000`. `B+0x4000`–`B+0x5000` `rw-p`, 오프셋 `0x3000`. `B+0x5000`–`B+0x805000` 익명 `rw-p` |

LOAD 4의 `p_vaddr=0x3d58`과 `p_offset=0x2d58`을 4 KiB 경계로 내리면 각각 `0x3000`, `0x2000`이다. `/proc/571/maps`에서 실제 시작 `0x5705c2c5e000`과 오프셋 `0x2000`이 일치했다. 이 첫 페이지는 `GNU_RELRO` 범위를 포함해 재배치 후 `r--p`이며, 다음 페이지는 쓰기 가능한 `.data`를 포함한다. LOAD 1은 정렬된 `p_vaddr`가 0이므로 첫 VMA 시작이 load bias와 같다.

`.bss`는 `readelf -SW`에서 `NOBITS`이며 LOAD 4에 있다. 8 MiB 배열의 시작 `0x5705c2c5f060`은 파일 경로가 붙은 마지막 `rw-p` 페이지 안에 있고, 마지막 바이트 `0x5705c345f05f`는 그 뒤의 익명 VMA 안에 있다. 도구는 그 익명 꼬리를 익명 범주에 합산했다.

### 4.3 구현 검증

WSL에서 빌드한 뒤 PID 모드가 대상을 살려 두는지, 공백이 있는 삭제된 실행 파일을 경로 그대로 표시하면서 주 실행 파일로 분류하는지 확인했다. 잘못된 인자, 없는 PID, 실행 실패, 첫 덤프 전과 두 번째 덤프 전 종료는 모두 비정상 종료 코드와 진단을 냈다. 필수 `smaps` 필드를 생략한 입력도 거부했다. SIGTERM을 무시하는 자식은 기본 대기와 정리 시간을 포함해 약 1.55초 만에 회수했다.

제출한 상세 덤프 27개에서 범주 합계와 권한 합계의 8개 열이 각각 `TOTAL`과 일치했다. 상세 행의 Size/Rss/Pss도 다시 합산해 대조했다. diff 3개에서 행 정렬과 범주 증감의 합계를 확인했다.

## 5. 필수 질문 답변 — 과제 명세 §5.5

**5.5-Q1. libc가 여러 VMA로 보이는 이유**  
ELF 로더는 파일의 서로 다른 오프셋에서 코드, 읽기 전용 데이터, 쓰기 가능한 데이터를 각 권한으로 매핑한다. 재배치 후 읽기 전용으로 보호하는 RELRO 영역은 별도 VMA를 만들 수 있다. [elf.txt](samples/elf.txt) 1회차의 libc에는 오프셋 `0x28000`의 `r-xp`, `0x1c0000`의 `r--p`, `0x212000`의 `rw-p` 등이 있다.

**5.5-Q2. Size가 Rss보다 훨씬 큰 영역**  
ELF의 익명 `.bss` 꼬리는 세 번 모두 `8,192/0` kB였고, 스레드 실험의 큰 익명 VMA는 `32,784/32` kB였다. ELF 워크로드는 배열 주소를 출력하는 동안 배열 내용을 읽거나 쓰지 않았으며, 워커도 스택 일부만 사용했다. 주소 범위를 확보해도 실제로 접근한 일부 페이지만 상주하게 된다는 가설과 일치한다.

**5.5-Q3. Rss와 Pss의 차이**  
Rss는 프로세스에 상주하는 공유 페이지도 전부 세고, Pss는 그 페이지의 크기를 공유하는 수로 나눠 더한다. ELF 1회차의 libc `r-xp` 영역은 Rss 1,184 kB, Pss 56 kB였다. 실행 코드 페이지가 다른 프로세스와 많이 공유되면 차이가 커진다. 같은 덤프의 `[heap]`은 두 값이 모두 12 kB였다.

**5.5-Q4. `.bss`와 `MemSiz > FileSiz`**  
LOAD 4의 메모리 크기 `0x800308`은 파일 크기 `0x02c0`보다 크다. 파일에서 읽은 바이트 뒤로 0으로 초기화되는 구간이 이어진다. §4.2에서 배열 앞부분은 마지막 파일 매핑 페이지에, 나머지는 이름 없는 `rw-p` 꼬리에 있었다.

**5.5-Q5. 반복 실행에서 바뀌는 주소와 고정된 오프셋**  
동일 PIE의 load bias는 `0x5705c2c5b000`, `0x5794c3f82000`, `0x60e7496bd000`이었다. 스택과 힙의 런타임 주소도 달랐다. `initialized_global`의 ELF 값 `0x4014`와 실행 파일 VMA의 오프셋 `0x0`, `0x1000`, `0x2000`, `0x2000`, `0x3000`은 세 실행에서 같았다. ASLR은 프로세스의 매핑 위치를 무작위화하며 동일 ELF의 파일 레이아웃은 고정돼 있다.

## 6. AI 사용 명세

Codex를 코드 작성, WSL 실험 실행, 출력 검증과 보고서 작성에 사용했다. 초기 구현에 도입한 `VMA`와 `Snapshot` 자료형을 검토한 뒤, 주소 범위를 키로 하는 딕셔너리로 바꿨다. 스켈레톤의 주석과 인자 안내 문구를 유지했고, 간소화한 소스로 다시 빌드해 실험 원본을 수집했다.
