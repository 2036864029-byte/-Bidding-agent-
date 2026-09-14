import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** 合并 Tailwind 类名（clsx + tailwind-merge），shadcn 约定工具 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
