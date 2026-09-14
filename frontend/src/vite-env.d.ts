/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 对话数据源：`api` = 直接连真实后端；缺省或其它值 = 演示数据（mock 流式） */
  readonly VITE_CHAT_MODE?: string;
  /** 后端基址；缺省 `/api` 走 Vite 代理（生产同域反代同理）。联调可直连 `http://host:8000` */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
