/**
 * 适配器统一出口 —— raw（后端原始）→ Domain（UI 消费）。
 *
 * 未来真数据就绪时，只改这里（及 mock/ 的替换）：页面组件与 types/ 契约零改动。
 */
export { kanbanAdapter } from './kanbanAdapter';
export { supplierAdapter } from './supplierAdapter';
export { chatResponseAdapter } from './chatAdapter';
export { referenceAdapter } from './referenceAdapter';
export { num, str, clamp, strArray, pick } from './helpers';
