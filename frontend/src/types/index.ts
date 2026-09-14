/**
 * 领域契约统一出口（Domain Model barrel）。
 *
 * 页面组件应从 `@/types` 取类型，从 `@/services/api` 取数据——不直接触碰后端字段。
 */
export * from './chat';
export * from './inquiry';
export * from './kanban';
export * from './supplier';
