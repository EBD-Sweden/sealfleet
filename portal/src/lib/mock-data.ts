// ─── MCP Servers ───────────────────────────────────────────────

export interface McpTool {
  name: string;
  description: string;
  inputSchema: Record<string, string>;
}

export interface McpServer {
  id: string;
  name: string;
  description: string;
  status: "online" | "offline" | "degraded";
  toolCount: number;
  owner: string;
  tags: string[];
  tools: McpTool[];
  lastSeen: string;
}

export const servers: McpServer[] = [
  {
    id: "crypto-gateway",
    name: "Crypto Gateway",
    description: "Real-time crypto price quotes and trade execution via major exchanges.",
    status: "online",
    toolCount: 4,
    owner: "Sealfleet Core",
    tags: ["crypto", "trading", "finance"],
    tools: [
      { name: "get_price", description: "Get real-time price for a trading pair", inputSchema: { pair: "string", exchange: "string?" } },
      { name: "execute_trade", description: "Execute a market or limit order", inputSchema: { pair: "string", side: "buy|sell", amount: "number" } },
      { name: "get_portfolio", description: "Retrieve current portfolio balances", inputSchema: { exchange: "string" } },
      { name: "get_order_status", description: "Check status of an open order", inputSchema: { orderId: "string" } },
    ],
    lastSeen: "2 min ago",
  },
  {
    id: "github-tools",
    name: "GitHub Tools",
    description: "Repository management, PR reviews, and issue tracking via GitHub API.",
    status: "online",
    toolCount: 5,
    owner: "DevOps Team",
    tags: ["devops", "git", "ci-cd"],
    tools: [
      { name: "list_repos", description: "List repositories for an org", inputSchema: { org: "string" } },
      { name: "create_pr", description: "Create a pull request", inputSchema: { repo: "string", title: "string", branch: "string" } },
      { name: "review_pr", description: "Add a review to a pull request", inputSchema: { repo: "string", prNumber: "number", body: "string" } },
      { name: "list_issues", description: "List open issues", inputSchema: { repo: "string", labels: "string[]?" } },
      { name: "create_issue", description: "Create a new issue", inputSchema: { repo: "string", title: "string", body: "string" } },
    ],
    lastSeen: "30 sec ago",
  },
  {
    id: "slack-bridge",
    name: "Slack Bridge",
    description: "Send messages, manage channels, and query conversation history.",
    status: "degraded",
    toolCount: 3,
    owner: "Platform Team",
    tags: ["messaging", "notifications"],
    tools: [
      { name: "send_message", description: "Send a message to a channel or DM", inputSchema: { channel: "string", text: "string" } },
      { name: "list_channels", description: "List available Slack channels", inputSchema: {} },
      { name: "search_messages", description: "Search message history", inputSchema: { query: "string", channel: "string?" } },
    ],
    lastSeen: "5 min ago",
  },
  {
    id: "postgres-query",
    name: "Postgres Query",
    description: "Read-only SQL queries against production analytics database.",
    status: "online",
    toolCount: 2,
    owner: "Data Team",
    tags: ["database", "analytics"],
    tools: [
      { name: "run_query", description: "Execute a read-only SQL query", inputSchema: { sql: "string", params: "any[]?" } },
      { name: "describe_table", description: "Get table schema information", inputSchema: { table: "string" } },
    ],
    lastSeen: "1 min ago",
  },
  {
    id: "email-sender",
    name: "Email Sender",
    description: "Compose and send transactional emails via SendGrid.",
    status: "offline",
    toolCount: 2,
    owner: "Marketing",
    tags: ["email", "notifications"],
    tools: [
      { name: "send_email", description: "Send a transactional email", inputSchema: { to: "string", subject: "string", body: "string" } },
      { name: "check_delivery", description: "Check email delivery status", inputSchema: { messageId: "string" } },
    ],
    lastSeen: "3 hours ago",
  },
];

// ─── Community / Discover Servers ──────────────────────────────

export interface CommunityServer {
  id: string;
  name: string;
  description: string;
  author: string;
  stars: number;
  installs: number;
  tags: string[];
}

export const communityServers: CommunityServer[] = [
  { id: "c1", name: "Weather MCP", description: "Global weather data from OpenWeatherMap", author: "weather-io", stars: 342, installs: 1200, tags: ["weather", "data"] },
  { id: "c2", name: "Stripe Payments", description: "Create charges, subscriptions, and refunds", author: "pay-tools", stars: 891, installs: 3400, tags: ["payments", "fintech"] },
  { id: "c3", name: "Notion Connector", description: "Read and write Notion pages and databases", author: "notion-mcp", stars: 567, installs: 2100, tags: ["productivity", "docs"] },
  { id: "c4", name: "AWS Lambda Runner", description: "Invoke and manage AWS Lambda functions", author: "cloud-tools", stars: 234, installs: 890, tags: ["cloud", "aws", "serverless"] },
  { id: "c5", name: "Jira Integration", description: "Create and manage Jira issues and sprints", author: "atlassian-mcp", stars: 445, installs: 1600, tags: ["project-mgmt", "agile"] },
  { id: "c6", name: "OpenAI Proxy", description: "Proxy tool calls through OpenAI models", author: "ai-bridge", stars: 1023, installs: 4200, tags: ["ai", "llm"] },
  { id: "c7", name: "S3 File Manager", description: "Upload, download, and list S3 objects", author: "cloud-tools", stars: 189, installs: 720, tags: ["cloud", "storage"] },
  { id: "c8", name: "Twilio SMS", description: "Send and receive SMS messages via Twilio", author: "comm-tools", stars: 312, installs: 1100, tags: ["messaging", "sms"] },
];

// ─── Audit Events ──────────────────────────────────────────────

export interface AuditEvent {
  id: string;
  timestamp: string;
  user: string;
  action: string;
  resource: string;
  server: string;
  result: "allowed" | "denied" | "error";
  traceId: string;
  durationMs: number;
}

export const auditEvents: AuditEvent[] = [
  { id: "evt-001", timestamp: "2026-02-23 14:32:01", user: "alice@corp.com", action: "get_price", resource: "BTC/USD", server: "Crypto Gateway", result: "allowed", traceId: "tr-a1b2c3", durationMs: 42 },
  { id: "evt-002", timestamp: "2026-02-23 14:31:55", user: "bob@corp.com", action: "execute_trade", resource: "ETH/USD", server: "Crypto Gateway", result: "denied", traceId: "tr-d4e5f6", durationMs: 8 },
  { id: "evt-003", timestamp: "2026-02-23 14:31:40", user: "alice@corp.com", action: "list_repos", resource: "mcpfinder-org", server: "GitHub Tools", result: "allowed", traceId: "tr-g7h8i9", durationMs: 210 },
  { id: "evt-004", timestamp: "2026-02-23 14:31:22", user: "charlie@corp.com", action: "send_message", resource: "#general", server: "Slack Bridge", result: "allowed", traceId: "tr-j0k1l2", durationMs: 95 },
  { id: "evt-005", timestamp: "2026-02-23 14:30:58", user: "diana@corp.com", action: "run_query", resource: "analytics.events", server: "Postgres Query", result: "allowed", traceId: "tr-m3n4o5", durationMs: 320 },
  { id: "evt-006", timestamp: "2026-02-23 14:30:45", user: "bob@corp.com", action: "send_email", resource: "user@example.com", server: "Email Sender", result: "error", traceId: "tr-p6q7r8", durationMs: 5002 },
  { id: "evt-007", timestamp: "2026-02-23 14:30:30", user: "eve@corp.com", action: "create_pr", resource: "mcpfinder/portal", server: "GitHub Tools", result: "allowed", traceId: "tr-s9t0u1", durationMs: 180 },
  { id: "evt-008", timestamp: "2026-02-23 14:30:12", user: "alice@corp.com", action: "get_portfolio", resource: "binance", server: "Crypto Gateway", result: "allowed", traceId: "tr-v2w3x4", durationMs: 67 },
  { id: "evt-009", timestamp: "2026-02-23 14:29:55", user: "frank@corp.com", action: "describe_table", resource: "users", server: "Postgres Query", result: "denied", traceId: "tr-y5z6a7", durationMs: 3 },
  { id: "evt-010", timestamp: "2026-02-23 14:29:40", user: "charlie@corp.com", action: "search_messages", resource: "#engineering", server: "Slack Bridge", result: "allowed", traceId: "tr-b8c9d0", durationMs: 145 },
  { id: "evt-011", timestamp: "2026-02-23 14:29:20", user: "diana@corp.com", action: "get_price", resource: "SOL/USD", server: "Crypto Gateway", result: "allowed", traceId: "tr-e1f2g3", durationMs: 38 },
  { id: "evt-012", timestamp: "2026-02-23 14:29:05", user: "bob@corp.com", action: "list_channels", resource: "workspace", server: "Slack Bridge", result: "allowed", traceId: "tr-h4i5j6", durationMs: 112 },
  { id: "evt-013", timestamp: "2026-02-23 14:28:50", user: "eve@corp.com", action: "review_pr", resource: "mcpfinder/gateway#42", server: "GitHub Tools", result: "allowed", traceId: "tr-k7l8m9", durationMs: 88 },
  { id: "evt-014", timestamp: "2026-02-23 14:28:35", user: "frank@corp.com", action: "execute_trade", resource: "BTC/EUR", server: "Crypto Gateway", result: "denied", traceId: "tr-n0o1p2", durationMs: 5 },
  { id: "evt-015", timestamp: "2026-02-23 14:28:10", user: "alice@corp.com", action: "check_delivery", resource: "msg-xyz-123", server: "Email Sender", result: "error", traceId: "tr-q3r4s5", durationMs: 3001 },
];

// ─── Dashboard Stats ───────────────────────────────────────────

export const dashboardStats = {
  totalServers: 5,
  totalTools: 16,
  activeUsers: 6,
  requestsToday: 1247,
  avgLatencyMs: 124,
  policyDenials: 3,
};

// ─── Recent Activity ───────────────────────────────────────────

export const recentActivity = [
  { text: "alice@corp.com called get_price on Crypto Gateway", time: "2 min ago" },
  { text: "Policy denied execute_trade for bob@corp.com", time: "3 min ago" },
  { text: "GitHub Tools server registered 2 new tools", time: "15 min ago" },
  { text: "Slack Bridge status changed to degraded", time: "22 min ago" },
  { text: "diana@corp.com ran analytics query (320ms)", time: "28 min ago" },
  { text: "Email Sender went offline", time: "3 hours ago" },
];
