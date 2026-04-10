import { X } from 'lucide-react';
import {
  NodeExecutionLog,
  WorkflowNodeData,
  NODE_CATEGORIES,
  NODE_LOGIC_DETAILS,
  NODE_TYPE_DEFINITIONS,
} from '../types/workflow';

function resolveBaseNodeId(canvasNodeId: string): string {
  const match = NODE_TYPE_DEFINITIONS.find(
    (def) => canvasNodeId === def.id || canvasNodeId.startsWith(`${def.id}_`)
  );
  return match?.id ?? canvasNodeId;
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface NodeDrawerProps {
  nodeId: string;
  nodeData: WorkflowNodeData | null;
  logs: NodeExecutionLog[];
  /** Last streamed `input` from `node_start` for this node id. */
  runInput?: unknown;
  /** Last streamed `output` from `node_done` (or failure payload from `node_failed`). */
  runOutput?: unknown;
  onClose: () => void;
}

export default function NodeDrawer({ nodeId, nodeData, logs, runInput, runOutput, onClose }: NodeDrawerProps) {
  if (!nodeData) return null;

  const categoryConfig = NODE_CATEGORIES[nodeData.category];
  const baseId = resolveBaseNodeId(nodeId);
  const details = NODE_LOGIC_DETAILS[baseId];

  return (
    <div className="w-[320px] bg-gray-900 border-l border-gray-800 h-screen overflow-y-auto flex flex-col">
      <div className="p-4 border-b border-gray-800 flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-2xl">{nodeData.icon}</span>
            <h2 className="text-lg font-bold text-white">{nodeData.label}</h2>
          </div>
          <div
            className="inline-block px-2 py-1 rounded text-xs font-medium"
            style={{
              backgroundColor: `${categoryConfig.color}20`,
              color: categoryConfig.color,
            }}
          >
            {categoryConfig.name}
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 hover:bg-gray-800 rounded transition-colors"
        >
          <X className="w-5 h-5 text-gray-400" />
        </button>
      </div>

      <div className="flex-1 p-4 space-y-6">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-2">Summary</label>
          <div className="w-full px-3 py-2 bg-gray-800 text-gray-300 text-sm rounded border border-gray-700">
            {details?.summary ?? nodeData.subtitle}
          </div>
        </div>

        {runInput !== undefined && (
          <div>
            <h3 className="text-sm font-semibold text-white mb-3">Last run — input</h3>
            <pre className="bg-gray-950 rounded p-3 text-[11px] font-mono text-slate-200 border border-gray-800 max-h-48 overflow-auto whitespace-pre-wrap break-words">
              {formatJson(runInput)}
            </pre>
          </div>
        )}

        {runOutput !== undefined && (
          <div>
            <h3 className="text-sm font-semibold text-white mb-3">
              Last run —{' '}
              {typeof runOutput === 'object' &&
              runOutput !== null &&
              'error' in (runOutput as object)
                ? 'failure'
                : 'output'}
            </h3>
            <pre
              className={`rounded p-3 text-[11px] font-mono max-h-64 overflow-auto whitespace-pre-wrap break-words border ${
                typeof runOutput === 'object' &&
                runOutput !== null &&
                'error' in (runOutput as object)
                  ? 'bg-red-950/40 text-red-100 border-red-800/60'
                  : 'bg-gray-950 text-emerald-200/90 border-emerald-900/50'
              }`}
            >
              {formatJson(runOutput)}
            </pre>
          </div>
        )}

        <div>
          <h3 className="text-sm font-semibold text-white mb-3">Backend Mapping</h3>
          <div className="bg-gray-950 rounded p-3 text-xs font-mono text-purple-300 border border-gray-800">
            {details?.pythonNode ?? 'N/A'}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-semibold text-white mb-3">Inputs</h3>
          <div className="space-y-2">
            {(details?.inputs ?? []).map((item) => (
              <div key={item} className="bg-gray-800 text-gray-200 text-xs px-3 py-2 rounded border border-gray-700">
                {item}
              </div>
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-semibold text-white mb-3">Outputs</h3>
          <div className="space-y-2">
            {(details?.outputs ?? []).map((item) => (
              <div key={item} className="bg-gray-800 text-gray-200 text-xs px-3 py-2 rounded border border-gray-700">
                {item}
              </div>
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-semibold text-white mb-3">Node Logic</h3>
          <div className="space-y-3">
            {(details?.logic ?? []).map((step, idx) => (
              <div key={`${step}-${idx}`} className="flex gap-2">
                <div className="w-5 h-5 rounded-full bg-blue-600 text-[10px] text-white flex items-center justify-center mt-0.5">
                  {idx + 1}
                </div>
                <p className="text-xs text-gray-300 leading-relaxed flex-1">{step}</p>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-semibold text-white mb-3">Execution logs</h3>
          <div className="bg-gray-950 rounded p-3 space-y-3 font-mono text-xs border border-gray-800 max-h-80 overflow-y-auto">
            {logs.length === 0 ? (
              <div className="text-gray-500">No logs yet. Run workflow to generate logs.</div>
            ) : (
              logs.map((log, idx) => (
                <div key={`${log.timestamp}-${idx}`} className="text-gray-300 border-b border-gray-800/80 pb-2 last:border-0 last:pb-0">
                  <div className="mb-1">
                    <span className="text-gray-500">{log.timestamp}</span>{' '}
                    <span
                      className={
                        log.level === 'SUCCESS'
                          ? 'text-green-400'
                          : log.level === 'DEBUG'
                            ? 'text-blue-400'
                            : log.level === 'ERROR'
                              ? 'text-red-400'
                              : 'text-yellow-400'
                      }
                    >
                      [{log.level}]
                    </span>
                  </div>
                  <div className="whitespace-pre-wrap break-words text-gray-300 leading-relaxed">{log.message}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
