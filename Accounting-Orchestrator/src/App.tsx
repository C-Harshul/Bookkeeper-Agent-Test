import { useCallback, useEffect, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  Connection,
  ReactFlowProvider,
  Node,
} from 'reactflow';
import 'reactflow/dist/style.css';

import CustomNode from './components/CustomNode';
import Sidebar from './components/Sidebar';
import Toolbar from './components/Toolbar';
import NodeDrawer from './components/NodeDrawer';
import { initialNodes, initialEdges } from './data/initialWorkflow';
import { NodeExecutionLog, WorkflowNodeData, NODE_TYPE_DEFINITIONS, WorkflowRunMode } from './types/workflow';

const nodeTypes = {
  custom: CustomNode,
};

function WorkflowCanvas() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [minimapVisible, setMinimapVisible] = useState(true);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [runMode, setRunMode] = useState<WorkflowRunMode>('auto');
  const [nodeLogs, setNodeLogs] = useState<Record<string, NodeExecutionLog[]>>({});
  /** Latest stream payload per canvas node id (shown in the drawer when the node is selected). */
  const [nodeRunInputs, setNodeRunInputs] = useState<Record<string, unknown>>({});
  const [nodeRunOutputs, setNodeRunOutputs] = useState<Record<string, unknown>>({});
  const [oauthEnabled, setOauthEnabled] = useState(false);
  const [oauthStatusMessage, setOauthStatusMessage] = useState(
    'OAuth disabled. Click to validate and enable QuickBooks access for required nodes.'
  );
  const [gmailTokensPresent, setGmailTokensPresent] = useState(false);
  const [gmailOAuthHelp, setGmailOAuthHelp] = useState<{
    redirectUri: string;
    javascriptOrigin: string;
    appOrigin: string;
    originMismatch: boolean;
  } | null>(null);
  const [gmailStatusMessage, setGmailStatusMessage] = useState(
    'Connect Gmail to load the latest inbox message into the Inspect Email node instead of the sample.'
  );
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  const redirectToOAuth = useCallback(async (authorizeUrl?: string) => {
    if (authorizeUrl) {
      window.location.href = authorizeUrl;
      return;
    }
    try {
      const res = await fetch('http://localhost:8000/oauth/quickbooks/authorize-url');
      if (!res.ok) return;
      const data = (await res.json()) as { authorizeUrl?: string };
      if (data.authorizeUrl) {
        window.location.href = data.authorizeUrl;
      }
    } catch {
      // no-op, user still has status message
    }
  }, []);

  const redirectToGmailOAuth = useCallback(async (authorizeUrl?: string) => {
    if (authorizeUrl) {
      window.location.href = authorizeUrl;
      return;
    }
    try {
      const res = await fetch('http://localhost:8000/oauth/gmail/authorize-url');
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg = (body as { detail?: { message?: string } })?.detail?.message ?? 'Gmail OAuth URL failed';
        setGmailStatusMessage(String(msg));
        return;
      }
      const data = (await res.json()) as { authorizeUrl?: string };
      if (data.authorizeUrl) {
        window.location.href = data.authorizeUrl;
      }
    } catch {
      setGmailStatusMessage('Could not reach backend for Gmail authorize URL.');
    }
  }, []);

  useEffect(() => {
    const loadGmailStatus = async () => {
      try {
        const res = await fetch('http://localhost:8000/oauth/gmail/status');
        if (!res.ok) return;
        const data = (await res.json()) as {
          oauthConfigured?: boolean;
          tokensPresent?: boolean;
          redirectUri?: string;
          javascriptOrigin?: string;
        };
        const appOrigin = window.location.origin;
        const js = (data.javascriptOrigin ?? '').trim();
        const redirect = (data.redirectUri ?? '').trim();
        setGmailOAuthHelp(
          redirect || js
            ? {
                redirectUri: redirect,
                javascriptOrigin: js,
                appOrigin,
                originMismatch: Boolean(js && appOrigin !== js),
              }
            : null
        );
        setGmailTokensPresent(Boolean(data.tokensPresent));
        if (!data.oauthConfigured) {
          setGmailStatusMessage('Backend missing GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REDIRECT_URI in .env.');
        } else if (data.tokensPresent) {
          setGmailStatusMessage('Gmail tokens found. Runs will use the latest inbox message.');
        } else {
          setGmailStatusMessage(
            'Click Enable Gmail OAuth. Google will show an account picker if you use multiple accounts.'
          );
        }
      } catch {
        // keep default message
      }
    };
    void loadGmailStatus();
  }, []);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, animated: true }, eds)),
    [setEdges]
  );

  const onNodeClick = useCallback((_event: React.MouseEvent, node: { id: string }) => {
    setSelectedNodeId(node.id);
  }, []);

  const selectedNode = selectedNodeId ? nodes.find((node) => node.id === selectedNodeId) ?? null : null;

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const type = event.dataTransfer.getData('application/reactflow');
      if (!type) return;

      const nodeDefinition = NODE_TYPE_DEFINITIONS.find((n) => n.id === type);
      if (!nodeDefinition) return;

      const reactFlowBounds = reactFlowWrapper.current?.getBoundingClientRect();
      if (!reactFlowBounds) return;

      const position = {
        x: event.clientX - reactFlowBounds.left - 110,
        y: event.clientY - reactFlowBounds.top,
      };

      const newNode: Node<WorkflowNodeData> = {
        id: `${type}_${Date.now()}`,
        type: 'custom',
        position,
        data: {
          label: nodeDefinition.label,
          icon: nodeDefinition.icon,
          subtitle: nodeDefinition.subtitle,
          category: nodeDefinition.category,
          requiresOAuth: false,
          oauthEnabled: true,
          status: 'idle',
        },
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [setNodes]
  );

  const enableGmailOAuth = useCallback(async () => {
    setGmailStatusMessage('Validating Gmail OAuth...');
    try {
      const response = await fetch('http://localhost:8000/oauth/gmail/enable', { method: 'POST' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = (body as { detail?: { code?: string; message?: string; authorizeUrl?: string } }).detail;
        if (response.status === 401 && detail?.code === 'GMAIL_NOT_CONNECTED') {
          setGmailStatusMessage('Gmail not connected. Redirecting to Google...');
          await redirectToGmailOAuth(detail?.authorizeUrl);
          return;
        }
        const message =
          detail?.message ?? (typeof body.detail === 'string' ? body.detail : '') ?? `Gmail enable failed (${response.status})`;
        throw new Error(message);
      }
      const data = (await response.json()) as { enabled?: boolean; emailAddress?: string };
      setGmailTokensPresent(true);
      setGmailStatusMessage(
        data.emailAddress
          ? `Gmail OK (${data.emailAddress}). Runs use the latest inbox message.`
          : 'Gmail validated. Runs use the latest inbox message.'
      );
    } catch (error) {
      setGmailStatusMessage(`Gmail: ${(error as Error).message}`);
    }
  }, [redirectToGmailOAuth]);

  const enableOAuthForRequiredNodes = useCallback(async () => {
    setOauthStatusMessage('Validating QuickBooks OAuth...');
    try {
      const response = await fetch('http://localhost:8000/oauth/quickbooks/enable', { method: 'POST' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        if (response.status === 401 && body?.detail?.code === 'QB_TOKEN_EXPIRED') {
          setOauthStatusMessage('QuickBooks token expired. Redirecting to OAuth authorization...');
          await redirectToOAuth(body?.detail?.authorizeUrl);
          return;
        }
        const message =
          body?.detail?.message ?? body?.detail ?? `OAuth enable failed (${response.status})`;
        throw new Error(message);
      }
      const data = (await response.json()) as { enabled: boolean; requiredNodeIds: string[] };
      const required = new Set(data.requiredNodeIds ?? []);
      const resolveBaseId = (id: string) => {
        const known = NODE_TYPE_DEFINITIONS.find((def) => id === def.id || id.startsWith(`${def.id}_`));
        return known?.id ?? id;
      };

      setNodes((nds) =>
        nds.map((node) => {
          const baseId = resolveBaseId(node.id);
          const requiresOAuth = required.has(baseId) || Boolean(node.data.requiresOAuth);
          return {
            ...node,
            data: {
              ...node.data,
              requiresOAuth,
              oauthEnabled: requiresOAuth ? true : node.data.oauthEnabled,
            },
          };
        })
      );

      setOauthEnabled(true);
      setOauthStatusMessage('QuickBooks OAuth validated and enabled for required nodes.');
    } catch (error) {
      setOauthEnabled(false);
      setOauthStatusMessage(`OAuth enable failed: ${(error as Error).message}`);
    }
  }, [redirectToOAuth, setNodes]);

  const runWorkflow = useCallback(() => {
    const formatOutput = (output: unknown, maxChars = 120_000) => {
      try {
        const asText = JSON.stringify(output, null, 2);
        return asText.length > maxChars ? `${asText.slice(0, maxChars)}\n...<truncated>` : asText;
      } catch {
        return String(output);
      }
    };

    const getEntryNodeId = () => {
      const incomingCount = new Map<string, number>();
      nodes.forEach((node) => incomingCount.set(node.id, 0));
      edges.forEach((edge) => {
        incomingCount.set(edge.target, (incomingCount.get(edge.target) ?? 0) + 1);
      });

      const roots = nodes.filter((node) => (incomingCount.get(node.id) ?? 0) === 0);
      if (roots.length === 0) return nodes[0]?.id;
      roots.sort((a, b) => {
        const byX = a.position.x - b.position.x;
        if (byX !== 0) return byX;
        return a.position.y - b.position.y;
      });
      return roots[0].id;
    };

    const appendNodeLog = (nodeId: string, level: NodeExecutionLog['level'], message: string, timestamp?: string) => {
      setNodeLogs((prev) => ({
        ...prev,
        [nodeId]: [
          ...(prev[nodeId] ?? []),
          {
            timestamp: timestamp ?? new Date().toLocaleTimeString(),
            level,
            message,
          },
        ],
      }));
    };

    const updateNodeStatus = (nodeId: string, status: WorkflowNodeData['status']) => {
      setNodes((nds) =>
        nds.map((node) => (node.id === nodeId ? { ...node, data: { ...node.data, status } } : node))
      );
    };

    const resetExecutionState = () => {
      setNodeLogs({});
      setNodeRunInputs({});
      setNodeRunOutputs({});
      setNodes((nds) =>
        nds.map((node) => ({
          ...node,
          data: { ...node.data, status: 'idle' },
        }))
      );
    };

    const runBackendStream = async () => {
      const response = await fetch('http://localhost:8000/run-workflow/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: runMode === 'auto' ? 'bill' : runMode,
          classification_mode: runMode === 'auto' ? 'llm' : 'scenario',
          emailSource: gmailTokensPresent ? 'gmail_latest' : 'sample',
          entryNodeId: getEntryNodeId(),
          nodes: nodes.map((node) => ({
            id: node.id,
            position: { x: node.position.x, y: node.position.y },
          })),
          edges: edges.map((edge) => ({
            source: edge.source,
            target: edge.target,
            label: typeof edge.label === 'string' ? edge.label : '',
          })),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = (body as { detail?: { code?: string; message?: string; authorizeUrl?: string } }).detail;
        if (response.status === 401 && detail?.code === 'GMAIL_NOT_CONNECTED' && detail?.authorizeUrl) {
          appendNodeLog('workflow', 'INFO', 'Gmail not connected. Redirecting to Google OAuth.', undefined);
          await redirectToGmailOAuth(detail.authorizeUrl);
          return;
        }
        const message =
          detail?.message ?? (typeof body.detail === 'string' ? body.detail : null) ?? `Backend error: ${response.status}`;
        throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
      }
      if (!response.body) {
        throw new Error('No response body from streaming endpoint');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line) as {
            event: string;
            nodeId?: string;
            timestamp?: string;
            log?: { level: NodeExecutionLog['level']; message: string };
            output?: unknown;
            input?: unknown;
            error?: string;
            message?: string;
            code?: string;
            authorizeUrl?: string;
            workflowFailed?: boolean;
            workflowFailureReason?: string;
          };

          if (event.event === 'workflow_start') {
            appendNodeLog('workflow', 'INFO', 'Workflow run started.', event.timestamp);
          } else if (event.event === 'node_start' && event.nodeId) {
            updateNodeStatus(event.nodeId, 'running');
            if (event.log) appendNodeLog(event.nodeId, event.log.level, event.log.message, event.timestamp);
            if (event.input !== undefined) {
              setNodeRunInputs((prev) => ({ ...prev, [event.nodeId!]: event.input }));
              const keys =
                event.input !== null && typeof event.input === 'object'
                  ? Object.keys(event.input as object)
                  : [];
              if (keys.length > 0) {
                appendNodeLog(event.nodeId, 'INFO', `Input:\n${formatOutput(event.input)}`, event.timestamp);
              }
            }
          } else if (event.event === 'node_done' && event.nodeId) {
            updateNodeStatus(event.nodeId, 'done');
            if (event.log) appendNodeLog(event.nodeId, event.log.level, event.log.message, event.timestamp);
            if (event.output !== undefined) {
              setNodeRunOutputs((prev) => ({ ...prev, [event.nodeId!]: event.output }));
              appendNodeLog(
                event.nodeId,
                'SUCCESS',
                `Output:\n${formatOutput(event.output)}`,
                event.timestamp
              );
            }
          } else if (event.event === 'node_failed' && event.nodeId) {
            updateNodeStatus(event.nodeId, 'failed');
            if (event.log) appendNodeLog(event.nodeId, event.log.level, event.log.message, event.timestamp);
            const failurePayload = {
              error: event.error,
              input: event.input,
            };
            setNodeRunOutputs((prev) => ({ ...prev, [event.nodeId!]: failurePayload }));
            if (event.error) {
              appendNodeLog(event.nodeId, 'ERROR', `Failure:\n${formatOutput(failurePayload)}`, event.timestamp);
            }
          } else if (event.event === 'node_skipped' && event.nodeId) {
            if (event.log) appendNodeLog(event.nodeId, event.log.level, event.log.message, event.timestamp);
          } else if (event.event === 'workflow_error') {
            appendNodeLog('workflow', 'INFO', event.message ?? 'Workflow execution failed.', event.timestamp);
            if (event.code === 'QB_TOKEN_EXPIRED') {
              appendNodeLog('workflow', 'INFO', 'Token expired. Redirecting to QuickBooks OAuth.');
              await redirectToOAuth(event.authorizeUrl);
              return;
            }
            if (event.code === 'GMAIL_NOT_CONNECTED' && event.authorizeUrl) {
              appendNodeLog('workflow', 'INFO', 'Gmail not connected. Redirecting to Google OAuth.');
              await redirectToGmailOAuth(event.authorizeUrl);
              return;
            }
          } else if (event.event === 'workflow_complete') {
            if (event.workflowFailed) {
              appendNodeLog(
                'workflow',
                'ERROR',
                `Workflow finished with failure: ${event.workflowFailureReason ?? 'unknown'}.`,
                event.timestamp
              );
            } else {
              appendNodeLog('workflow', 'SUCCESS', 'Workflow completed.', event.timestamp);
            }
          }
        }
      }
    };

    resetExecutionState();
    runBackendStream()
      .catch((error: Error) => {
        appendNodeLog('workflow', 'INFO', `Run failed: ${error.message}. Ensure backend is running on port 8000.`);
      });
  }, [edges, gmailTokensPresent, nodes, redirectToGmailOAuth, redirectToOAuth, runMode, setNodes]);

  return (
    <div className="flex h-screen bg-slate-950">
      <Sidebar
        oauthEnabled={oauthEnabled}
        oauthStatusMessage={oauthStatusMessage}
        onEnableOAuth={enableOAuthForRequiredNodes}
        gmailTokensPresent={gmailTokensPresent}
        gmailStatusMessage={gmailStatusMessage}
        onEnableGmail={enableGmailOAuth}
        gmailOAuthHelp={gmailOAuthHelp}
      />

      <div className="flex-1 flex flex-col">
        <Toolbar
          onRun={runWorkflow}
          runMode={runMode}
          onRunModeChange={setRunMode}
          minimapVisible={minimapVisible}
          onToggleMinimap={() => setMinimapVisible(!minimapVisible)}
        />

        <div ref={reactFlowWrapper} className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            fitView
            attributionPosition="bottom-left"
          >
            <Background color="#334155" gap={16} size={1} className="bg-slate-950" />
            <Controls className="bg-gray-800 border-gray-700" />
            {minimapVisible && (
              <MiniMap
                className="bg-gray-800 border border-gray-700"
                maskColor="rgb(15, 23, 42, 0.8)"
                nodeColor={(node) => {
                  const data = node.data as WorkflowNodeData;
                  const colors: Record<string, string> = {
                    trigger: '#3b82f6',
                    ai: '#8b5cf6',
                    'data-fetch': '#f97316',
                    action: '#22c55e',
                    router: '#64748b',
                    terminal: '#ef4444',
                  };
                  return colors[data.category] || '#666';
                }}
              />
            )}
          </ReactFlow>
        </div>
      </div>

      {selectedNode && (
        <NodeDrawer
          nodeId={selectedNode.id}
          nodeData={selectedNode.data}
          logs={nodeLogs[selectedNode.id] ?? []}
          runInput={nodeRunInputs[selectedNode.id]}
          runOutput={nodeRunOutputs[selectedNode.id]}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
    </div>
  );
}

function GmailOAuthCallbackPage() {
  const [status, setStatus] = useState('Completing Gmail authorization...');

  useEffect(() => {
    const run = async () => {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('code');
      if (!code) {
        setStatus('Authorization code missing. Please retry Gmail OAuth.');
        return;
      }

      try {
        const response = await fetch('http://localhost:8000/oauth/gmail/callback-exchange', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code,
            redirectUri: `${window.location.origin}/gmail-callback`,
          }),
        });

        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          const detail = (body as { detail?: { message?: string } }).detail;
          throw new Error(detail?.message ?? `Exchange failed (${response.status})`);
        }

        setStatus('Gmail connected. Redirecting to workflow...');
        setTimeout(() => {
          window.location.href = '/';
        }, 900);
      } catch (error) {
        setStatus(`Authorization failed: ${(error as Error).message}`);
      }
    };

    void run();
  }, []);

  return (
    <div className="h-screen w-screen bg-slate-950 text-slate-100 flex items-center justify-center">
      <div className="max-w-xl rounded-xl border border-slate-700 bg-slate-900 p-6">
        <h1 className="text-xl font-semibold mb-3">Gmail OAuth</h1>
        <p className="text-sm text-slate-300 whitespace-pre-wrap">{status}</p>
      </div>
    </div>
  );
}

function OAuthCallbackPage() {
  const [status, setStatus] = useState('Completing QuickBooks authorization...');

  useEffect(() => {
    const run = async () => {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('code');
      const realmId = params.get('realmId');
      if (!code) {
        setStatus('Authorization code missing. Please retry OAuth flow.');
        return;
      }

      try {
        const response = await fetch('http://localhost:8000/oauth/quickbooks/callback-exchange', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code,
            realmId,
            redirectUri: `${window.location.origin}/callback`,
          }),
        });

        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body?.detail?.message ?? `Exchange failed (${response.status})`);
        }

        setStatus('Authorization successful. Redirecting back to workflow...');
        setTimeout(() => {
          window.location.href = '/';
        }, 900);
      } catch (error) {
        setStatus(`Authorization failed: ${(error as Error).message}`);
      }
    };

    void run();
  }, []);

  return (
    <div className="h-screen w-screen bg-slate-950 text-slate-100 flex items-center justify-center">
      <div className="max-w-xl rounded-xl border border-slate-700 bg-slate-900 p-6">
        <h1 className="text-xl font-semibold mb-3">QuickBooks OAuth</h1>
        <p className="text-sm text-slate-300 whitespace-pre-wrap">{status}</p>
      </div>
    </div>
  );
}

function App() {
  const path = window.location.pathname;
  if (path === '/gmail-callback') {
    return <GmailOAuthCallbackPage />;
  }
  if (path === '/callback') {
    return <OAuthCallbackPage />;
  }

  return (
    <ReactFlowProvider>
      <WorkflowCanvas />
    </ReactFlowProvider>
  );
}

export default App;
