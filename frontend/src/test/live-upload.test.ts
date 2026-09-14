// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { api, setDataSource } from '@/services/api';
import type { UploadedFileState } from '@/services/api';

/**
 * 真实后端联调（上传 + 解析轮询 + 带文件提问）—— **默认跳过**。
 * 跑法：起后端，然后
 * `VITE_API_BASE=http://127.0.0.1:8000 npx vitest run src/test/live-upload.test.ts`
 *
 * ⚠️ 必须用 **node 环境**：jsdom 的 FormData/File 与 Node undici 的 fetch 不同源，
 * 会把 multipart 请求体序列化成空的（表现为后端回 `file: Field required`）。
 */
const LIVE = Boolean(import.meta.env?.VITE_API_BASE);

describe.skipIf(!LIVE)('真实后端 · 上传与带文件提问', () => {
  it('异步上传 → 轮询到 done → 带 file_ids 提问走 doc_qa', async () => {
    setDataSource('chat', 'api');

    const md = [
      '# 联调招标文件',
      '',
      '## 第三章 投标保证金',
      '',
      '本项目投标保证金金额为人民币柒拾贰万元整（720000元）。',
      '',
    ].join('\n');
    const file = new File([md], 'live-probe.md', { type: 'text/markdown' });

    const seen: string[] = [];
    let fileId: string | undefined;
    // 模拟 store 的行为：会话为空时**采纳上传回带的会话**（先传文件后提问的关键）
    let sessionId: string | null = null;

    await api.chat.upload(file, 'probe-key', sessionId, (u: UploadedFileState) => {
      if (seen[seen.length - 1] !== u.status) seen.push(u.status);
      if (u.sessionId && !sessionId) sessionId = u.sessionId;
      if (u.status === 'done') fileId = u.fileId;
    });

    expect(seen[0]).toBe('pending');
    expect(seen).toContain('done');
    expect(fileId).toBeTruthy();
    expect(sessionId).toBeTruthy(); // 上传必须回带会话，否则文件归属会丢

    const res = await api.chat.send('本项目投标保证金是多少？', {
      sessionId: sessionId ?? undefined,
      fileIds: [fileId as string],
    });
    expect(res.replyType).toBe('doc_qa');
    expect(res.data.kind).toBe('doc_qa');
    expect(res.answer).toContain('720000');
  }, 180_000);
});
