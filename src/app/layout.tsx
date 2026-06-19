import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'moly 음성 대화 파이프라인 테스트 UI',
  description: '텍스트 인풋 → 스트리밍 텍스트 아웃풋',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
