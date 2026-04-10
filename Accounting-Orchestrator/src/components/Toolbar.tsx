import { Play, Save, ZoomIn, ZoomOut, Maximize, Map } from 'lucide-react';
import { useReactFlow } from 'reactflow';
import { WorkflowRunMode } from '../types/workflow';

interface ToolbarProps {
  onRun: () => void;
  runMode: WorkflowRunMode;
  onRunModeChange: (value: WorkflowRunMode) => void;
  minimapVisible: boolean;
  onToggleMinimap: () => void;
}

export default function Toolbar({
  onRun,
  runMode,
  onRunModeChange,
  minimapVisible,
  onToggleMinimap,
}: ToolbarProps) {
  const { zoomIn, zoomOut, fitView } = useReactFlow();

  return (
    <div className="h-14 bg-gray-900 border-b border-gray-800 flex items-center justify-between px-4">
      <div className="flex items-center gap-2">
        <button
          onClick={onRun}
          className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg flex items-center gap-2 font-medium transition-colors"
        >
          <Play className="w-4 h-4" />
          Run Workflow
        </button>

        <button
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg flex items-center gap-2 font-medium transition-colors"
        >
          <Save className="w-4 h-4" />
          Save
        </button>
        <select
          value={runMode}
          onChange={(e) => onRunModeChange(e.target.value as WorkflowRunMode)}
          className="px-3 py-2 bg-gray-800 text-gray-100 rounded-lg border border-gray-700 text-sm max-w-[220px]"
        >
          <option value="auto">Auto — LLM classify</option>
          <option value="bill">Simulate bill path</option>
          <option value="invoice">Simulate invoice path</option>
          <option value="no_action">Simulate no-action path</option>
        </select>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => zoomIn()}
          className="p-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
          title="Zoom In"
        >
          <ZoomIn className="w-4 h-4" />
        </button>

        <button
          onClick={() => zoomOut()}
          className="p-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
          title="Zoom Out"
        >
          <ZoomOut className="w-4 h-4" />
        </button>

        <button
          onClick={() => fitView()}
          className="p-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
          title="Fit View"
        >
          <Maximize className="w-4 h-4" />
        </button>

        <button
          onClick={onToggleMinimap}
          className={`p-2 rounded-lg transition-colors ${
            minimapVisible
              ? 'bg-blue-600 hover:bg-blue-700 text-white'
              : 'bg-gray-700 hover:bg-gray-600 text-white'
          }`}
          title="Toggle Minimap"
        >
          <Map className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
