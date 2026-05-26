import ReviewPageClient from "@/components/ReviewPageClient";
import { fetchExtractionProfileServer, fetchPipelineStatusServer, fetchRequirementsServer } from "@/lib/review-server";

type Props = {
  params: Promise<{ docId: string }>;
};

export default async function ReviewPage({ params }: Props) {
  const { docId } = await params;
  const [initialRequirements, initialPipelineStatus, initialExtractionProfile] = await Promise.all([
    fetchRequirementsServer(docId),
    fetchPipelineStatusServer(docId),
    fetchExtractionProfileServer(docId),
  ]);

  return (
    <ReviewPageClient
      docId={docId}
      initialRequirements={initialRequirements}
      initialPipelineStatus={initialPipelineStatus}
      initialExtractionProfile={initialExtractionProfile}
    />
  );
}
