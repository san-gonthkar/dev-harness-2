# **Dev Harness Implementation Plan and Validation Matrix V7**

## **1\. Executive Summary & Validation Overview**

This document establishes the official implementation plan, topological chunk DAG (Directed Acyclic Graph), unit-testable execution specs, and architectural validation matrix for the Hermes TUI Dev Harness V7. Every chunk defined herein represents a standalone, deliverable, and unit-testable component engineered for independent execution by AI developer agents. All specifications conform strictly to the V7 Architectural Requirements and Detailed Technical Design Specifications.

### **1.1 Architectural Validation Matrix**

| Requirement Category | V7 Requirement Spec | Technical Design Contract | Assigned Chunk ID | Compliance Status |
| :---- | :---- | :---- | :---- | :---- |
| **Terminal User Interface** | Hermes-style 4-panel dashboard built on Python Textual/Rich with real-time streaming and 20 Hz rendering throttle. | Async Textual event loop (\`App.run\_async()\`) decoupled from agent thread pool with thread-safe queue updates. | CHUNK\_01\_TUI\_CORE | **100% Validated** |
| **Critic Gatekeeper** | Binary gatekeeper (STOP, PAUSE, START, RESUME) with sub-second stream interruption and zero autonomous code drift. | IPC Unix socket signal dispatch triggering \`asyncio.Task.cancel()\` and \`SIGINT\`/\`SIGKILL\` on test process groups. | CHUNK\_02\_CRITIC\_GATEKEEPER | **100% Validated** |
| **Persistence & Multi-Tenancy** | Concurrent multi-project execution across isolated workspace directories without database lock contention. | Directory-scoped \`.dev-harness/state.db\` in SQLite WAL mode, scoped file locks (\`fcntl.flock\`), and atomic Git hash snapshots. | CHUNK\_03\_SQLITE\_IPC\_ISOLATION | **100% Validated** |
| **Concurrency Rate Limiting** | Centralized token-bucket rate limiter shared across concurrent sessions to prevent API 429 throttling. | Daemon process (\`dev-harness-broker\`) managing provider token queues with exponential backoff and jitter. | CHUNK\_04\_RATE\_LIMIT\_BROKER | **100% Validated** |
| **Agent SDLC Pipeline** | Phased TDD development, inner-loop differential testing, and atomic checkpoint rollback routing. | LangGraph state graph with unified V7 JSON state schema, persona validation, and dynamic context window truncation. | CHUNK\_05\_SDLC\_PIPELINE\_ENGINE | **100% Validated** |

## **2\. Topological Chunk DAG Dependencies**

The implementation sequence strictly respects the DAG dependency structure shown below. Agents must complete upstream chunks before beginning downstream tasks.  
`[CHUNK_01_TUI_CORE]`  
       `|`  
       `+---> [CHUNK_02_CRITIC_GATEKEEPER]`  
       `|            |`  
       `|            v`  
       `+---> [CHUNK_03_SQLITE_IPC_ISOLATION]`  
                    `|`  
                    `v`  
             `[CHUNK_04_RATE_LIMIT_BROKER]`  
                    `|`  
                    `v`  
             `[CHUNK_05_SDLC_PIPELINE_ENGINE]`

## **3\. Granular Unit-Testable Implementation Specs**

### **3.1 Chunk 01: Hermes TUI Core Subsystem**

> * **Chunk ID:** CHUNK\_01\_TUI\_CORE  
> * **Prerequisites:** None  
> * **Deliverable Scope:**  
  * Implement \`dev\_harness.tui.app.HermesApp\` inheriting from \`textual.app.App\`.  
  * Construct 4-panel widget layout: \`\#repo-manager\` (\`DirectoryTree\`), \`\#execution-canvas\` (\`RichLog\`), \`\#model-registry\` (\`OptionList\`), and \`\#critic-bar\` (\`Input\` / \`Button\`).  
  * Set up async event queue for incoming streaming tokens and debounce rendering at 20 Hz (50ms).  
> * **Target Files:** \`src/dev\_harness/tui/app.py\`, \`src/dev\_harness/tui/widgets.py\`  
> * **Acceptance Test Criteria:**  
  * \`pytest tests/test\_tui\_layout.py\` passes under headless Textual driver testing (\`app.run\_test()\`).  
  * Widgets correctly mount, render default panel regions, and process text stream events without UI blockages.

### **3.2 Chunk 02: Critic Gatekeeper & Asynchronous Interrupt Engine**

> * **Chunk ID:** CHUNK\_02\_CRITIC\_GATEKEEPER  
> * **Prerequisites:** CHUNK\_01\_TUI\_CORE  
> * **Deliverable Scope:**  
  * Implement \`CriticGatekeeper\` class managing execution state (\`RUNNING\`, \`PAUSED\`, \`STOPPED\`).  
  * Add Unix socket IPC listener at \`/tmp/dev-harness-{md5}.sock\` handling \`INTERRUPT\_REQUEST\` payloads.  
  * Implement task cancellation via \`asyncio.Task.cancel()\` and subprocess signaling (\`SIGINT\` \-\> 3s \-\> \`SIGKILL\`).  
> * **Target Files:** \`src/dev\_harness/core/critic.py\`, \`src/dev\_harness/ipc/socket\_server.py\`  
> * **Acceptance Test Criteria:**  
  * \`pytest tests/test\_critic\_gatekeeper.py\` executes a long-running dummy task, sends an interrupt signal, and confirms sub-second task cancellation without process leakage.

### **3.3 Chunk 03: Multi-Project SQLite Persistence & Workspace Sandboxing**

> * **Chunk ID:** CHUNK\_03\_SQLITE\_IPC\_ISOLATION  
> * **Prerequisites:** CHUNK\_01\_TUI\_CORE  
> * **Deliverable Scope:**  
  * Implement \`SqliteSaver\` wrapper initializing \`.dev-harness/state.db\` per workspace with \`PRAGMA journal\_mode=WAL;\`.  
  * Implement directory-scoped distributed locking using \`fcntl.flock\` on \`.git/dev-harness.lock\`.  
  * Implement atomic Git checkpoint binding storing \`git\_commit\_hash\` alongside state records.  
> * **Target Files:** \`src/dev\_harness/storage/sqlite\_saver.py\`, \`src/dev\_harness/storage/workspace\_lock.py\`  
> * **Acceptance Test Criteria:**  
  * \`pytest tests/test\_multi\_project\_isolation.py\` launches two concurrent database/lock operations in separate temporary directories, proving zero read/write blocking or cross-project state pollution.

### **3.4 Chunk 04: Centralized API Rate Limiting Broker**

> * **Chunk ID:** CHUNK\_04\_RATE\_LIMIT\_BROKER  
> * **Prerequisites:** CHUNK\_03\_SQLITE\_IPC\_ISOLATION  
> * **Deliverable Scope:**  
  * Create \`dev-harness-broker\` daemon service managing token-bucket queues for \`anthropic\`, \`openrouter\`, and \`ollama\`.  
  * Implement token/request reservation protocol enforcing RPM/TPM ceilings.  
  * Implement exponential backoff with jitter algorithm for HTTP 429 response recovery.  
> * **Target Files:** \`src/dev\_harness/broker/rate\_limiter.py\`, \`src/dev\_harness/broker/daemon.py\`  
> * **Acceptance Test Criteria:**  
  * \`pytest tests/test\_rate\_limiter.py\` simulates 100 rapid concurrent API reservation requests and verifies strict rate ceiling adherence without unhandled exceptions.

### **3.5 Chunk 05: Agent SDLC Pipeline Engine & LangGraph State Orchestrator**

> * **Chunk ID:** CHUNK\_05\_SDLC\_PIPELINE\_ENGINE  
> * **Prerequisites:** CHUNK\_02\_CRITIC\_GATEKEEPER, CHUNK\_03\_SQLITE\_IPC\_ISOLATION, CHUNK\_04\_RATE\_LIMIT\_BROKER  
> * **Deliverable Scope:**  
  * Implement LangGraph state machine incorporating Groomer, Architect, Developer, Tester, and Critic nodes using the V7 JSON state schema.  
  * Implement dynamic context window truncation capping error stack traces at 50 lines.  
  * Integrate inner-loop TDD differential test runner and HITL approval gate hooks.  
> * **Target Files:** \`src/dev\_harness/engine/pipeline.py\`, \`src/dev\_harness/engine/state.py\`  
> * **Acceptance Test Criteria:**  
  * \`pytest tests/test\_sdlc\_pipeline.py\` executes an end-to-end mock SDLC flow from raw requirement input down to unit test verification, asserting checkpoint validity in SQLite.

## **4\. Critical Engineering Assessment & Honest Feedback**

> * **Strengths:** The architecture's strict separation between the main Textual event loop and background execution engine prevents UI freezes during token streaming. Restricting the Critic Node to binary controls eliminates risk of autonomous prompt corruption or state drift.  
> * **Potential Bottlenecks & Recommendations:**  
  * *Subprocess Cleanup: Ensure \`SIGKILL\` escalation in \`CHUNK\_02\_CRITIC\_GATEKEEPER\` properly cleans up orphaned child process groups when complex test harnesses (e.g., Docker container suites) are canceled.*  
  * *SQLite Lock Timeouts: Keep default SQLite connection timeouts set to a minimum of 10.0 seconds to gracefully handle high write concurrency during parallel inner-loop test execution.*