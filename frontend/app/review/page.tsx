import { redirect } from "next/navigation";

/** /review → 홈 (docId 없는 404 방지) */
export default function ReviewIndexPage() {
  redirect("/");
}
