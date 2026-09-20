# **Hermes TUI Dev Harness \- Detailed Technical Design Specification (V7)**

## **1\. System Architecture & Foundation**

This Technical Design Document defines the concrete implementation specifications for the Hermes-style Terminal User Interface (TUI) Software Development Harness ("Dev Harness") based on the V7 architectural requirements. It details low-level software contracts, asynchronous event loops, process boundary controls, inter-process communication (IPC) channels, persistence isolation, and token-bucket concurrency rate limiting to enable agentic SDLC execution.

## **2\. Hermes TUI Event Loop & UI Subsystem Specifications**

The TUI control plane is built on Python's Textual asynchronous TUI framework, leveraging Rich renderables for real-time terminal output formatting. The UI operates on an asynchronous event loop decoupled from agent execution threads.

### **2.1 Async Event Loop & Worker Separation**

> * **UI Thread:** Runs the primary Textual App.run\_async() event loop on the main thread, handling layout rendering, keypress events, widget state updates, and terminal resize signals.  
> * **Agent Execution Engine:** Runs in a background asyncio event loop managed by a dedicated worker process or detached thread pool. Agent state nodes communicate with the TUI via thread-safe asyncio.Queue and IPC channels.  
> * **FPS & Rendering Throttle:** UI widget update events are debounced at 20 Hz (50ms interval) using Textual's set\_interval to prevent terminal render thrashing during high-volume token streaming.

### **2.2 Four-Panel Widget Layout Protocol**

| Panel Widget ID | Textual Component Class | Data Stream Protocol | Rendering Logic |
| :---- | :---- | :---- | :---- |
| \#repo-manager | DirectoryTree & DataTable | Subscribes to IPC workspace status events (\`FILE\_CHANGE\`, \`GIT\_STATUS\_UPDATE\`). | Displays active Git branch, uncommitted diff count, workspace path, and project session list. |
| \#execution-canvas | RichLog & Sparkline | Consumes streaming token chunks (\`AGENT\_TOKEN\_STREAM\`) and AST execution events (\`TEST\_PROGRESS\`). | Renders Markdown streams, colored diff highlights, and live execution progress bars. |
| \#model-registry | OptionList & Static | Reads from active model provider state (\`MODEL\_CONFIG\_CHANGE\`). | Shows active LLM endpoints (Qwen 2.5 Coder, Claude, OpenRouter), latency metrics, and API token burn rates. |
| \#critic-bar | Input & Button | Publishes pause/resume commands (\`INTERRUPT\_SIGNAL\`) to the engine. | Hosts the real-time input bar, binary pause/resume toggles (\`PAUSE\`, \`RESUME\`), and HITL gate buttons. |

## **3\. Universal Critic Gatekeeper & Asynchronous Thread Cancellation**

The Critic Node functions strictly as a **binary gatekeeper** capable of pausing or resuming agent execution without modifying code or requirements autonomously.

### **3.1 Interrupt Protocol & Signal Propagation**

> 1. **User Trigger:** The user presses \`Ctrl+C\` or clicks the \`PAUSE\` button in \`\#critic-bar\`.  
> 2. **Event Dispatch:** The TUI emits an \`INTERRUPT\_REQUEST\` payload over the IPC gRPC/Unix socket channel to the execution engine.  
> 3. **Task Cancellation:** The execution loop catches the signal and invokes \`asyncio.Task.cancel()\` on active agent worker tasks.  
> 4. **Process Signals:** For external subprocesses (e.g., test runners or compiler calls), the engine issues a \`SIGINT\` to the process group, falling back to \`SIGKILL\` after a 3.0-second timeout.  
> 5. **Checkpoint Seal:** Upon task cancellation, the \`SqliteSaver\` captures an exact state snapshot with \`is\_paused \= True\` and commits the current Git hash (\`git\_commit\_hash\`).

### **3.2 Critic Gatekeeper State Machine**

\[RUNNING\] \---\> (User Interrupt / Gate Trigger) \---\> \[PAUSED\]  
    ^                                                   |  
    |                                                   v  
    \+---------------- (Human Resume Command) \-----------+

## **4\. Persistence, Isolation & Multi-Project State Database**

To eliminate cross-talk and state corruption during simultaneous project execution, persistence and locking are strictly multi-tenant and directory-scoped.

### **4.1 SQLite State Database Isolation (\`SqliteSaver\`)**

> * **Storage Architecture:** Each workspace maintains an isolated SQLite database at \`.dev-harness/state.db\` within its root directory. Alternatively, a centralized instance partitions tables using \`(project\_id, thread\_id)\` composite primary keys.  
> * **WAL Mode Enforcement:** Databases execute \`PRAGMA journal\_mode=WAL;\` and \`PRAGMA synchronous=NORMAL;\` upon connection initialization to allow concurrent reader/writer access across TUI processes without database locking errors.  
> * **Schema Namespacing:** All checkpoint queries enforce explicit filtering on \`project\_id \= :project\_id AND thread\_id \= :thread\_id\`.

### **4.2 Multi-Project IPC & Workspace Sandboxing**

> * **Unix Domain Sockets (IPC):** TUI sessions communicate with background daemon engines via workspace-scoped sockets located at \`/tmp/dev-harness-{md5(workspace\_path)}.sock\`.  
> * **Directory-Scoped Distributed Locks:** Concurrency locks are managed using file-based locks (\`fcntl.flock\` / \`portalocker\`) at \`.git/dev-harness.lock\`. Worker threads in Project A cannot acquire or interfere with locks in Project B.  
> * **Git State Boundary:** Checkpoints store atomic Git commit hashes (\`git\_commit\_hash\`). Restoring a state checkpoint automatically executes \`git checkout {git\_commit\_hash}\` within the target workspace directory.

## **5\. Centralized API Rate Limiting Protocol**

To prevent HTTP 429 rate limit exceptions and local inference server (Ollama/vLLM) memory saturation during parallel multi-project execution, all API requests pass through a centralized rate-limiting broker.

### **5.1 Token-Bucket Rate Limiter Specification**

> * **Shared Broker Process:** A lightweight background daemon (\`dev-harness-broker\`) exposes an IPC endpoint managing token bucket queues per provider (\`anthropic\`, \`openrouter\`, \`ollama\`).  
> * **Rate Limit Parameters:**  
  * **Requests Per Minute (RPM):** Configurable ceiling per provider (e.g., 50 RPM for Anthropic Tier 2).  
  * **Tokens Per Minute (TPM):** Dynamic token allocation based on estimated prompt length \+ max output tokens.  
> * **Backoff Strategy:** On HTTP 429 response, the broker applies Exponential Backoff with Jitter: \`t\_wait \= min(max\_backoff, base \* 2^attempt) \+ random\_jitter\`.

## **6\. Complete System State Schema (V7)**

The system state is governed by a unified JSON-serializable structure managed by LangGraph reducers and persisted in SQLite.  
{  
  "project\_id": "proj\_98234\_backend",  
  "workspace\_path": "/home/user/projects/backend-api",  
  "thread\_id": "thread\_session\_2026\_09\_20\_001",  
  "raw\_input": "string",  
  "groomed\_requirements": {  
    "status": "LOCKED",  
    "prd\_content": "string",  
    "version": "V7",  
    "locked\_at\_timestamp": 1758332765  
  },  
  "technical\_design": {  
    "architecture\_spec": "string",  
    "interface\_contracts": {  
      "openapi\_spec": "string",  
      "db\_schema": "string"  
    },  
    "status": "APPROVED"  
  },  
  "chunk\_dag": \[  
    {  
      "chunk\_id": "chunk\_auth\_module",  
      "title": "Authentication Interface Implementation",  
      "dependencies": \[\],  
      "status": "PENDING | IN\_PROGRESS | COMPLETED | FAILED",  
      "assigned\_worker\_id": "worker\_01"  
    }  
  \],  
  "tui\_state": {  
    "active\_panel": "WORKSPACE | CANVAS | REGISTRY | CRITIC",  
    "active\_model\_provider": "Qwen 2.5 Coder CPU 7B | Claude Code | OpenRouter",  
    "is\_paused": false,  
    "last\_interrupt\_timestamp": 1758332765,  
    "critic\_gatekeeper\_status": "READY | PAUSED | RUNNING"  
  },  
  "git\_state": {  
    "active\_branch": "main",  
    "last\_checkpoint\_commit": "a1b2c3d4e5f67890123456789abcdef012345678"  
  },  
  "rate\_limiting": {  
    "allocated\_tokens\_tpm": 15000,  
    "current\_rpm\_count": 12  
  },  
  "inner\_loop\_retry\_count": 0,  
  "e2e\_retry\_count": 0,  
  "latest\_e2e\_report": {  
    "classification": "CHUNK\_IMPLEMENTATION\_BUG | INTEGRATION\_SPEC\_MISMATCH",  
    "stack\_trace": "string",  
    "failed\_chunk\_id": "string"  
  }  
}