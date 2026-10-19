-- AlterTable
ALTER TABLE "tasks" ADD COLUMN     "current_step" TEXT,
ADD COLUMN     "diff_hash" TEXT;

-- CreateTable
CREATE TABLE "agent_events" (
    "id" UUID NOT NULL,
    "task_id" UUID NOT NULL,
    "type" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "agent_events_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "agent_events_task_id_created_at_idx" ON "agent_events"("task_id", "created_at");

-- AddForeignKey
ALTER TABLE "agent_events" ADD CONSTRAINT "agent_events_task_id_fkey" FOREIGN KEY ("task_id") REFERENCES "tasks"("id") ON DELETE CASCADE ON UPDATE CASCADE;
