import { beforeEach, describe, expect, it } from 'vitest';
import {
  chunkText,
  citationByIndex,
  citationNumberFromHref,
  clearSessionId,
  errorText,
  linkifyCitations,
  loadSessionId,
  saveSessionId,
  shouldSurfaceError,
  validateUpload,
} from '@/lib/chat';
import type { Citation } from '@/types/chat';

/* 对话模块纯逻辑：流式切片 / 引用标记 / 错误码文案 / 会话持久化 / 上传预校验。 */

describe('chunkText', () => {
  it('按指定长度切片', () => {
    expect(chunkText('abcdefghij', 4)).toEqual(['abcd', 'efgh', 'ij']);
  });

  it('按码点切，不劈开代理对（emoji / 生僻字）', () => {
    const chunks = chunkText('a😀b', 1);
    expect(chunks).toEqual(['a', '😀', 'b']);
    expect(chunks.join('')).toBe('a😀b');
  });

  it('空串返回空数组', () => {
    expect(chunkText('')).toEqual([]);
  });
});

describe('linkifyCitations / citationNumberFromHref', () => {
  it('把【来源N】转成 #cite-N 链接', () => {
    expect(linkifyCitations('见【来源1】与【来源2】')).toBe('见[【来源1】](#cite-1)与[【来源2】](#cite-2)');
  });

  it('从 href 解析编号', () => {
    expect(citationNumberFromHref('#cite-3')).toBe(3);
    expect(citationNumberFromHref('#other')).toBeNull();
  });

  it('无标记的文本原样返回', () => {
    expect(linkifyCitations('普通回答')).toBe('普通回答');
  });
});

describe('citationByIndex', () => {
  const cites: Citation[] = [
    { contextIndex: 1, chunkId: 1, chunkUid: 'a', docName: 'A', chapter: '', chunkIndex: 0, text: '甲', score: 1, metadata: {} },
    { contextIndex: 2, chunkId: 2, chunkUid: 'b', docName: 'B', chapter: '', chunkIndex: 0, text: '乙', score: 1, metadata: {} },
  ];

  it('按 contextIndex 精确匹配', () => {
    expect(citationByIndex(cites, 2)?.docName).toBe('B');
  });

  it('编号越界时按下标兜底', () => {
    const sparse: Citation[] = [{ ...cites[0], contextIndex: 5 }];
    expect(citationByIndex(sparse, 1)?.docName).toBe('A');
  });
});

describe('errorText', () => {
  it('给出人话文案', () => {
    expect(errorText('sys_2004')).toContain('请求较多');
    expect(errorText('biz_1006')).toContain('未授权');
  });

  it('biz_1004 / biz_1005 不打扰用户（答案本身已含引导）', () => {
    expect(shouldSurfaceError('biz_1004')).toBe(false);
    expect(shouldSurfaceError('biz_1005')).toBe(false);
    expect(errorText('biz_1004')).toBeNull();
  });

  it('未知错误码降级成通用文案而非空白', () => {
    expect(errorText('weird_9999')).toContain('weird_9999');
  });

  it('无错误码返回 null', () => {
    expect(errorText(null)).toBeNull();
  });
});

describe('会话持久化', () => {
  beforeEach(clearSessionId);

  it('存取与清除', () => {
    expect(loadSessionId()).toBeNull();
    saveSessionId('sess-123');
    expect(loadSessionId()).toBe('sess-123');
    clearSessionId();
    expect(loadSessionId()).toBeNull();
  });
});

describe('validateUpload', () => {
  /** 造一个指定大小的 File（size 是只读属性，用 defineProperty 覆盖） */
  const file = (name: string, mb: number): File => {
    const f = new File(['x'], name);
    Object.defineProperty(f, 'size', { value: mb * 1024 * 1024 });
    return f;
  };

  it('放行白名单内且不超限的文件', () => {
    expect(validateUpload(file('招标文件.pdf', 3))).toBeNull();
    expect(validateUpload(file('照片.PNG', 1))).toBeNull();
  });

  it('扩展名不符直接拦下', () => {
    expect(validateUpload(file('恶意.exe', 1))).toContain('不支持的文件类型');
  });

  it('超过 20MB 拦下', () => {
    expect(validateUpload(file('大文件.pdf', 21))).toContain('20 MB');
  });
});
