import { useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Dialog from "@/components/ui/Dialog";
import Notification from "@/components/ui/Notification";
import toast from "@/components/ui/toast";
import {
  HiOutlineTrash,
  HiOutlineDownload,
  HiOutlineUpload,
  HiOutlineEye,
} from "react-icons/hi";
import { useTranslation } from "react-i18next";
import {
  apiUploadClintDocument,
  apiGetClintDocuments,
  apiDeleteClintDocument,
  apiDownloadClintDocument,
} from "@/services/ClientsService";
import { useAppSelector } from "../store";

type ClintDocument = {
  id: string;
  original_name: string;
  display_name: string | null;
  file_type: string;
  create_at: string;
};

const IMAGE_TYPES = ["jpg", "jpeg", "png"];

const Documents = () => {
  const { t } = useTranslation();
  const clint = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData,
  );

  const [documents, setDocuments] = useState<ClintDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [customName, setCustomName] = useState("");

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const fetchDocuments = async () => {
    if (!clint?.id) return;
    setLoading(true);
    try {
      const response = await apiGetClintDocuments(clint.id);
      setDocuments(response.data.documents || []);
    } catch (error) {
      toast.push(
        <Notification title={t("documents.fetchFail")} type="danger" />,
        { placement: "top-center" },
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clint?.id]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const allowedExt = [".pdf", ".jpg", ".jpeg", ".png"];
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!allowedExt.includes(ext)) {
      toast.push(
        <Notification title={t("documents.invalidType")} type="danger" />,
        { placement: "top-center" },
      );
      e.target.value = "";
      return;
    }

    setPendingFile(file);
    setCustomName("");
  };

  const handleUpload = async () => {
    if (!pendingFile || !clint?.id) return;

    const formData = new FormData();
    formData.append("file", pendingFile);
    if (customName.trim()) {
      formData.append("name", customName.trim());
    }

    setUploading(true);
    try {
      const response = await apiUploadClintDocument(clint.id, formData);
      if (response.data.status) {
        toast.push(
          <Notification title={t("documents.uploadSuccess")} type="success" />,
          { placement: "top-center" },
        );
        setPendingFile(null);
        setCustomName("");
        fetchDocuments();
      } else {
        toast.push(
          <Notification title={t("documents.uploadFail")} type="danger">
            {response.data.message}
          </Notification>,
          { placement: "top-center" },
        );
      }
    } catch (error) {
      toast.push(
        <Notification title={t("documents.uploadFail")} type="danger" />,
        { placement: "top-center" },
      );
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (docId: string) => {
    try {
      const response = await apiDeleteClintDocument(docId);
      if (response.data.status) {
        toast.push(
          <Notification title={t("documents.deleteSuccess")} type="success" />,
          { placement: "top-center" },
        );
        setDocuments((prev) => prev.filter((d) => d.id !== docId));
      } else {
        toast.push(
          <Notification title={t("documents.deleteFail")} type="danger" />,
          { placement: "top-center" },
        );
      }
    } catch (error) {
      toast.push(
        <Notification title={t("documents.deleteFail")} type="danger" />,
        { placement: "top-center" },
      );
    }
  };

  const handleOpenFile = async (doc: ClintDocument) => {
    setPreviewLoading(true);
    try {
      const response = await apiDownloadClintDocument(doc.id);
      const blob = new Blob([response.data]);
      const url = window.URL.createObjectURL(blob);

      if (IMAGE_TYPES.includes(doc.file_type)) {
        setPreviewUrl(url);
        setPreviewOpen(true);
      } else {
        window.open(url, "_blank");
      }
    } catch (error) {
      toast.push(
        <Notification title={t("documents.downloadFail")} type="danger" />,
        { placement: "top-center" },
      );
    } finally {
      setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    setPreviewOpen(false);
    if (previewUrl) {
      window.URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 rounded-lg border border-gray-300 p-5">
        <div className="flex items-center gap-3">
          <label>
            <input
              type="file"
              accept=".pdf,.jpg,.jpeg,.png"
              className="hidden"
              onChange={handleFileChange}
              disabled={uploading || clint?.is_archive || clint?.is_locked}
            />
            <Button
              variant="default"
              size="sm"
              disabled={uploading || clint?.is_archive || clint?.is_locked}
              onClick={(e) => {
                (e.currentTarget.previousSibling as HTMLElement)?.click();
              }}
            >
              {t("documents.chooseFile")}
            </Button>
          </label>
          {pendingFile && (
            <span className="text-[13.5px] text-gray-600">{pendingFile.name}</span>
          )}
        </div>

        {pendingFile && (
          <div className="flex items-center gap-3 mt-2">
            <Input
              size="sm"
              placeholder={t("documents.nameOptional")}
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
            />
            <Button
              variant="solid"
              size="sm"
              loading={uploading}
              icon={<HiOutlineUpload />}
              onClick={handleUpload}
            >
              {t("documents.upload")}
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-gray-300">
        {loading ? (
          <div className="p-5 text-center text-[15px] text-gray-600">{t("loading")}</div>
        ) : documents.length === 0 ? (
          <div className="p-5 text-center text-[15px] text-gray-600">
            {t("documents.empty")}
          </div>
        ) : (
          documents.map((doc, index) => (
            <div
              key={doc.id}
              className={`flex min-h-[44px] items-center justify-between gap-3 px-5 py-2 transition-colors duration-150 hover:bg-gray-50 ${
                index !== documents.length - 1 ? "border-b border-gray-200" : ""
              }`}
            >
              <div className="flex min-w-0 items-center gap-3">
                <button
                  className="text-red-600 hover:text-red-700"
                  onClick={() => handleDelete(doc.id)}
                  title={t("delete")}
                >
                  <HiOutlineTrash size={16} />
                </button>
                <button
                  className="text-link"
                  onClick={() => handleOpenFile(doc)}
                  disabled={previewLoading}
                  title={
                    IMAGE_TYPES.includes(doc.file_type)
                      ? t("documents.preview")
                      : t("documents.download")
                  }
                >
                  {IMAGE_TYPES.includes(doc.file_type) ? (
                    <HiOutlineEye size={16} />
                  ) : (
                    <HiOutlineDownload size={16} />
                  )}
                </button>
                <span
                  className="truncate text-[15px] text-gray-700"
                  title={doc.display_name || doc.original_name}
                >
                  {doc.display_name || doc.original_name}
                </span>
              </div>
              <span
                dir="ltr"
                className="shrink-0 text-[13.5px] tabular-nums text-gray-600"
              >
                {doc.create_at?.substring(0, 10)}
              </span>
            </div>
          ))
        )}
      </div>

      <Dialog
        isOpen={previewOpen}
        onClose={closePreview}
        onRequestClose={closePreview}
        width={700}
      >
        <div className="flex justify-center items-center p-2">
          {previewUrl && (
            <img
              src={previewUrl}
              alt="preview"
              className="max-h-[75vh] max-w-full rounded-lg"
            />
          )}
        </div>
      </Dialog>
    </div>
  );
};

export default Documents;
