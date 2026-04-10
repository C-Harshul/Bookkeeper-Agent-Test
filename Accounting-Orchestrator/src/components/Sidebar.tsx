import { useState } from 'react';
import { ChevronDown, ChevronRight, Workflow } from 'lucide-react';
import { NODE_TYPE_DEFINITIONS, NODE_CATEGORIES } from '../types/workflow';

interface SidebarProps {
  oauthEnabled: boolean;
  oauthStatusMessage: string;
  onEnableOAuth: () => void;
  gmailTokensPresent: boolean;
  gmailStatusMessage: string;
  onEnableGmail: () => void;
  gmailOAuthHelp: {
    redirectUri: string;
    javascriptOrigin: string;
    appOrigin: string;
    originMismatch: boolean;
  } | null;
}

export default function Sidebar({
  oauthEnabled,
  oauthStatusMessage,
  onEnableOAuth,
  gmailTokensPresent,
  gmailStatusMessage,
  onEnableGmail,
  gmailOAuthHelp,
}: SidebarProps) {
  const [nodesOpen, setNodesOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const onDragStart = (event: React.DragEvent, nodeType: string) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className="w-[260px] bg-gray-900 border-r border-gray-800 flex flex-col h-screen">
      <div className="p-4 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Workflow className="w-6 h-6 text-blue-500" />
          <h1 className="text-lg font-bold text-white">
            Accounting Orchestrator
          </h1>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="border-b border-gray-800 p-3">
          <button
            onClick={onEnableOAuth}
            className={`w-full px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
              oauthEnabled
                ? 'bg-green-700 hover:bg-green-600 text-white'
                : 'bg-blue-600 hover:bg-blue-500 text-white'
            }`}
          >
            {oauthEnabled ? 'QuickBooks OAuth Enabled' : 'Enable QuickBooks OAuth'}
          </button>
          <p className="text-xs text-gray-400 mt-2">{oauthStatusMessage}</p>
        </div>

        <div className="border-b border-gray-800 p-3 space-y-2">
          {gmailOAuthHelp && (gmailOAuthHelp.redirectUri || gmailOAuthHelp.javascriptOrigin) && (
            <div
              className={`rounded-md border p-2 text-[10px] leading-relaxed ${
                gmailOAuthHelp.originMismatch
                  ? 'border-amber-700/80 bg-amber-950/50 text-amber-100'
                  : 'border-gray-700 bg-gray-800/80 text-gray-300'
              }`}
            >
              <p className="font-semibold text-gray-200">Google Cloud (exact strings)</p>
              <p className="mt-1 text-gray-500">Authorized redirect URIs</p>
              <code className="mt-0.5 block break-all text-emerald-400/90">
                {gmailOAuthHelp.redirectUri || '— set GMAIL_REDIRECT_URI in .env'}
              </code>
              <p className="mt-2 text-gray-500">Authorized JavaScript origins</p>
              <code className="mt-0.5 block break-all text-emerald-400/90">
                {gmailOAuthHelp.javascriptOrigin || '—'}
              </code>
              {gmailOAuthHelp.originMismatch && (
                <p className="mt-2 text-amber-200">
                  <span className="font-medium">Port/host mismatch:</span> this tab is{' '}
                  <code className="text-white">{gmailOAuthHelp.appOrigin}</code> but <code className="text-white">.env</code>{' '}
                  expects the origin above. That causes <span className="whitespace-nowrap">redirect_uri_mismatch</span>.
                  Set <code className="text-white">GMAIL_REDIRECT_URI</code> to{' '}
                  <code className="break-all text-white">
                    {gmailOAuthHelp.appOrigin}/gmail-callback
                  </code>{' '}
                  and add the same values in Google Console, then restart the API.
                </p>
              )}
            </div>
          )}
          <button
            onClick={onEnableGmail}
            className={`w-full px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
              gmailTokensPresent
                ? 'bg-emerald-700 hover:bg-emerald-600 text-white'
                : 'bg-slate-600 hover:bg-slate-500 text-white'
            }`}
          >
            {gmailTokensPresent ? 'Gmail connected (read inbox)' : 'Enable Gmail OAuth'}
          </button>
          <p className="text-xs text-gray-400 mt-2">{gmailStatusMessage}</p>
        </div>

        <div className="border-b border-gray-800">
          <button
            onClick={() => setNodesOpen(!nodesOpen)}
            className="w-full px-4 py-3 flex items-center justify-between text-sm font-medium text-gray-300 hover:bg-gray-800 transition-colors"
          >
            <span>Nodes</span>
            {nodesOpen ? (
              <ChevronDown className="w-4 h-4" />
            ) : (
              <ChevronRight className="w-4 h-4" />
            )}
          </button>

          {nodesOpen && (
            <div className="p-2 space-y-2">
              {NODE_TYPE_DEFINITIONS.map((node) => {
                const categoryConfig = NODE_CATEGORIES[node.category];
                return (
                  <div
                    key={node.id}
                    draggable
                    onDragStart={(e) => onDragStart(e, node.id)}
                    className="p-2 bg-gray-800 rounded cursor-move hover:bg-gray-700 transition-colors border-l-2"
                    style={{ borderLeftColor: categoryConfig.color }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{node.icon}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium text-white truncate">
                          {node.label}
                        </div>
                        <div
                          className="text-xs mt-0.5"
                          style={{ color: categoryConfig.color }}
                        >
                          {categoryConfig.name}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-b border-gray-800">
          <button
            onClick={() => setSettingsOpen(!settingsOpen)}
            className="w-full px-4 py-3 flex items-center justify-between text-sm font-medium text-gray-300 hover:bg-gray-800 transition-colors"
          >
            <span>Settings</span>
            {settingsOpen ? (
              <ChevronDown className="w-4 h-4" />
            ) : (
              <ChevronRight className="w-4 h-4" />
            )}
          </button>

          {settingsOpen && (
            <div className="p-4">
              <p className="text-xs text-gray-500 italic">
                No settings configured
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
