import Table from "@/components/ui/Table";
import { useTranslation } from "react-i18next";
import Card from "@/components/ui/Card";
import { getSpecifyUsers, useAppDispatch, useAppSelector } from "../store";
import { useEffect } from "react";
import { NumericFormat } from "react-number-format";
import {apiGetCdrByDate,apiGetCdrRecordingPath} from "@/services/ClientsService";
import { useState } from "react";
import { HiOutlineVolumeUp, HiOutlineDownload } from "react-icons/hi";
import Button from "@/components/ui/Button";
import StatusPill from "@/components/shared/StatusPill";

const { Tr, Th, Td, THead, TBody } = Table;

/* CDR dispositions carry a status tone so the column reads at a glance;
   the label itself stays whatever `callsRecord.<disposition>` translates to. */
const dispositionTone = (
  disposition: string
): "success" | "warning" | "danger" | "neutral" => {
  switch (String(disposition).toUpperCase()) {
    case "ANSWERED":
      return "success";
    case "NO ANSWER":
    case "BUSY":
      return "warning";
    case "FAILED":
      return "danger";
    default:
      return "neutral";
  }
};

const CallsRecordClient = () => {
  const { t } = useTranslation();
  const { permissions } = useAppSelector((state) => state.auth.user);

  const [CDRByDate, setCDRByDate] = useState([]);
  const [currentAudio, setCurrentAudio] = useState<HTMLAudioElement | null>(null);
  const [playingRecording, setPlayingRecording] = useState<string | null>(null);

  

  const dispatch = useAppDispatch();
  const client = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.clientData
  );
    const clintContacts = useAppSelector(
    (state) => state.crmCustomerDetails?.data?.ContactData
  );


useEffect(() => {
  if (!client?.update_at || !clintContacts?.length) return;
  
  //current date
  const currentDate = new Date();
  const enddate = new Date(currentDate);
  // client.update_at like Mon, 09 Sep 2024 10:03:31 GMT convert to YYYY-MM-DD 00:00:00
  const startDate = new Date(client.update_at);
  const numbers = [];
  clintContacts.forEach((contact) => {
    if (contact?.contact && contact?.contact !== "") {

      numbers.push(contact.contact);
    }
  });
  startDate.setHours(0, 0, 0, 0); 
  //send date in YYYY-MM-DD hh:mm:ss format
  const formattedStartDate = startDate.toISOString().slice(0, 19).replace("T", " ");
  const formattedEndDate = enddate.toISOString().slice(0, 19).replace("T", " ");
  fetchCdrByDate(formattedStartDate, formattedEndDate, numbers);
}, [client?.update_at, clintContacts?.length]); // More specific dependencies

  //----------------------------------

  const fetchCdrByDate = async (startDate: string, endDate: string, numbers: string[]) => {
    try {
      const response = await apiGetCdrByDate({ startDate, endDate, numbers });
      // Sort by calldate in descending order (newest first)
      const sortedData = response.data.sort((a, b) => {
        const dateA = new Date(a.calldate);
        const dateB = new Date(b.calldate);
        return dateB.getTime() - dateA.getTime();
      });
      setCDRByDate(sortedData);
    } catch (error) {
      console.error("Error fetching CDR by date range:", error);
    }
  };

 const handleDownloadRecording = async (recordingPath: string , account : string) => {
  try {
    const response = await apiGetCdrRecordingPath({ recordingPath });
    const blob = new Blob([response.data], { type: "audio/wav" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${account}.wav`; // Changed to .wav extension
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url); // Clean up the object URL
  } catch (error) {
    console.error("Error downloading recording:", error);
  }
};

const handlePlayRecording = async (recordingPath: string) => {
  try {
    // Stop any currently playing audio
    if (currentAudio) {
      currentAudio.pause();
      currentAudio.currentTime = 0;
      setCurrentAudio(null);
      setPlayingRecording(null);
    }

    // If clicking the same recording that was playing, just stop
    if (playingRecording === recordingPath) {
      return;
    }

    const response = await apiGetCdrRecordingPath({ recordingPath });
    const blob = new Blob([response.data], { type: "audio/wav" });
    const url = window.URL.createObjectURL(blob);
    
    const audio = new Audio(url);
    setCurrentAudio(audio);
    setPlayingRecording(recordingPath);
    
    audio.onended = () => {
      setCurrentAudio(null);
      setPlayingRecording(null);
      window.URL.revokeObjectURL(url);
    };
    
    audio.onerror = () => {
      console.error("Error playing audio");
      setCurrentAudio(null);
      setPlayingRecording(null);
      window.URL.revokeObjectURL(url);
    };
    
    await audio.play();
  } catch (error) {
    console.error("Error playing recording:", error);
    setPlayingRecording(null);
  }
};


  return (
    <div>
      <div className="mt-8">
        <Table compact>
          <THead>
            <Tr>
              <Th>{t("callsRecord.callDate")}</Th>
              <Th>{t("callsRecord.callTime")}</Th>
              <Th>{t("callsRecord.caller")}</Th>
              <Th>{t("callsRecord.receiver")}</Th>
              <Th className="text-end">{t("callsRecord.duration")}</Th>
               <Th className="text-end">{t("callsRecord.callDuration")}</Th>
              <Th>{t("callsRecord.status")}</Th>
              <Th>{t("callsRecord.recordingfile")}</Th>
            </Tr>
          </THead>
          <TBody>
            {CDRByDate?.map((call, i) => (
              <Tr key={i}>
                <Td className="tabular-nums">
                  {call?.calldate
                    ? new Date(call.calldate).toISOString().slice(0, 10)
                    : ""}
                </Td>
                <Td className="tabular-nums">
                  {call?.calldate
                    ? new Date(call.calldate).toISOString().slice(11, 19)
                    : ""}
                </Td>
                <Td>
                  <span dir="ltr" className="tabular-nums">
                    {call?.src ? call.src.slice(-10) : ""}
                  </span>
                </Td>
                <Td>
                  <span dir="ltr" className="tabular-nums">
                    {call?.dst ? call.dst.slice(-10) : ""}
                  </span>
                </Td>
                <Td className="text-end tabular-nums">
                  {call?.duration
                    ? `${Math.floor(call.duration / 60)}:${(call.duration % 60)
                        .toString()
                        .padStart(2, "0")}`
                    : "00:00"}
                </Td>
                <Td className="text-end tabular-nums">
                  {call.billsec >= 0
                    ? `${Math.floor(call.billsec / 60)}:${(call.billsec % 60)
                        .toString()
                        .padStart(2, "0")}`
                    : call?.duration
                    ? `${Math.floor(call.duration / 60)}:${(call.duration % 60)
                        .toString()
                        .padStart(2, "0")}`
                    : "00:00"}
                </Td>
                <Td>
                  {call?.disposition ? (
                    <StatusPill tone={dispositionTone(call.disposition)}>
                      {t(`callsRecord.${call.disposition}`)}
                    </StatusPill>
                  ) : (
                    ""
                  )}
                </Td>
                <Td>
                  <div className="flex items-center gap-2">
                    {permissions.clints_callsRecords_download && (
                      <Button
                        size="xs"
                        variant="default"
                        onClick={() =>
                          handleDownloadRecording(
                            call?.recordingfile,
                            client?.accountNumber
                          )
                        }
                        icon={<HiOutlineDownload />}
                      ></Button>
                    )}
                    {permissions.clints_callsRecords_play && (
                      <Button
                        size="xs"
                        variant="default"
                        className={
                          playingRecording === call?.recordingfile
                            ? "!text-red-600"
                            : ""
                        }
                        onClick={() => handlePlayRecording(call?.recordingfile)}
                        icon={<HiOutlineVolumeUp />}
                        disabled={!call?.recordingfile}
                      ></Button>
                    )}
                  </div>
                </Td>
              
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>
    </div>
  );
};

export default CallsRecordClient;
