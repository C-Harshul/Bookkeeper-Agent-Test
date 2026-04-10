import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { WorkflowNodeData, NODE_CATEGORIES } from '../types/workflow';

function CustomNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  const categoryConfig = NODE_CATEGORIES[data.category];

  const getStatusColor = () => {
    switch (data.status) {
      case 'running':
        return 'bg-yellow-500';
      case 'done':
        return 'bg-green-500';
      case 'failed':
        return 'bg-red-600';
      default:
        return 'bg-gray-500';
    }
  };

  const getStatusText = () => {
    switch (data.status) {
      case 'running':
        return 'Running';
      case 'done':
        return 'Done';
      case 'failed':
        return 'Failed';
      default:
        return 'Idle';
    }
  };

  return (
    <div
      className={`relative bg-gray-800 rounded-lg shadow-lg transition-all duration-200 hover:shadow-xl hover:-translate-y-0.5 ${
        selected ? 'ring-2 ring-offset-2 ring-offset-gray-900' : ''
      }`}
      style={{
        borderLeft: `4px solid ${categoryConfig.color}`,
        width: '220px',
        ...(selected && { boxShadow: `0 0 0 2px ${categoryConfig.color}` }),
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="w-3 h-3 !bg-gray-600 border-2 border-gray-400"
      />

      <div className="p-3">
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2 flex-1">
            <span className="text-2xl">{data.icon}</span>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-white truncate">
                {data.label}
              </h3>
              {data.requiresOAuth && (
                <div
                  className={`mt-1 inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                    data.oauthEnabled
                      ? 'bg-green-900/50 text-green-300 border border-green-700'
                      : 'bg-amber-900/50 text-amber-300 border border-amber-700'
                  }`}
                >
                  {data.oauthEnabled ? 'OAuth Enabled' : 'OAuth Required'}
                </div>
              )}
            </div>
          </div>
          <div
            className={`px-2 py-0.5 rounded-full text-xs font-medium text-white ${getStatusColor()}`}
          >
            {getStatusText()}
          </div>
        </div>

        <p className="text-xs text-gray-400 leading-relaxed">
          {data.subtitle}
        </p>

        <div
          className="mt-2 px-2 py-1 rounded text-xs font-medium inline-block"
          style={{ backgroundColor: `${categoryConfig.color}20`, color: categoryConfig.color }}
        >
          {categoryConfig.name}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="w-3 h-3 !bg-gray-600 border-2 border-gray-400"
      />
    </div>
  );
}

export default memo(CustomNode);
