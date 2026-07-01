import { redirect } from "next/navigation";

type Props = {
  params: Promise<{ docId: string }>;
};

// 옛 리뷰 페이지(AI 판정·Human 판정 UI)는 조견표 편집(/edit)으로 통합·deprecate.
// 북마크·구 링크·demo 라우트가 /review 로 와도 /edit 로 넘긴다. (ReviewPageClient 코드는 보존)
export default async function ReviewPage({ params }: Props) {
  const { docId } = await params;
  redirect(`/edit/${docId}`);
}
