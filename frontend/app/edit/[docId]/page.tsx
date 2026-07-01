import EditTableClient from "@/components/EditTableClient";

type Props = { params: Promise<{ docId: string }> };

export default async function EditPage({ params }: Props) {
  const { docId } = await params;
  return <EditTableClient docId={docId} />;
}
