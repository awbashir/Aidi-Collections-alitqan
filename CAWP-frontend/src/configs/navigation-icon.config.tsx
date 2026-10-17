import {
  HiOutlineColorSwatch,
  HiOutlineDesktopComputer,
  HiOutlineTemplate,
  HiOutlineViewGridAdd,
  HiOutlineHome,
  HiOutlineUser,
  HiOutlineBriefcase,
  HiOutlineDocumentReport,
} from "react-icons/hi";
import { LiaFileContractSolid } from "react-icons/lia";
import {
  MdOutlineHandshake,
  MdOutlineAccountBalanceWallet,
} from "react-icons/md";
import { BsFillPersonLinesFill, BsBuildings } from "react-icons/bs";
import { RiArchiveDrawerLine } from "react-icons/ri";
import { PiMoney } from "react-icons/pi";
import { IoSettingsOutline } from "react-icons/io5";
import { GrSecure } from "react-icons/gr";
import { BsSearch } from "react-icons/bs";
import {
  Activity,
  CircleAlert,
  CalendarClock,
  Handshake,
  ClipboardCheck,
  FilePenLine,
  ListChecks,
  CalendarRange,
  Target,
  Inbox,
  FileChartColumn,
  Phone,
  Headset,
  Megaphone,
  ShieldAlert,
  SlidersHorizontal,
  PhoneOff, // blocked clients
  Mic, // Phase 6 Q3: voice training lab
  BrainCircuit, // Phase 6: AI administration
  BadgeCheck, // Phase 6: quality workspace
} from "lucide-react";



export type NavigationIcons = Record<string, JSX.Element>;

const navigationIcon: NavigationIcons = {
  home: <HiOutlineHome />,
  singleMenu: <HiOutlineViewGridAdd />,
  collapseMenu: <HiOutlineTemplate />,
  groupSingleMenu: <HiOutlineDesktopComputer />,
  groupCollapseMenu: <HiOutlineColorSwatch />,
  user: <HiOutlineUser />,
  contract: <LiaFileContractSolid />,
  portfolio: <HiOutlineBriefcase />,
  client: <BsFillPersonLinesFill />,
  archive: <RiArchiveDrawerLine />,
  branch_supervisor: <BsBuildings />,
  accounting: <MdOutlineAccountBalanceWallet />,
  collection: <PiMoney />,
  reports: <HiOutlineDocumentReport />,
  settings: <IoSettingsOutline />,
  permission: <GrSecure />,
  client_search: <BsSearch />,
  /* Phase 1 (Lucide, 2px stroke, currentColor). */
  attention: <CircleAlert strokeWidth={2} size={18} />,
  follow_ups: <CalendarClock strokeWidth={2} size={18} />,
  promises: <Handshake strokeWidth={2} size={18} />,
  payment_approvals: <ClipboardCheck strokeWidth={2} size={18} />,
  corrections: <FilePenLine strokeWidth={2} size={18} />,
  /* Phase 2 */
  bulk_jobs: <ListChecks strokeWidth={2} size={18} />,
  /* Phase 3 */
  plans: <CalendarRange strokeWidth={2} size={18} />,
  team: <Target strokeWidth={2} size={18} />,
  requests: <Inbox strokeWidth={2} size={18} />,
  /* Phase 4 */
  reports_hub: <FileChartColumn strokeWidth={2} size={18} />,
  /* Phase 5 */
  calls: <Phone strokeWidth={2} size={18} />,
  calling: <Headset strokeWidth={2} size={18} />,
  campaigns: <Megaphone strokeWidth={2} size={18} />,
  call_review: <ShieldAlert strokeWidth={2} size={18} />,
  calling_admin: <SlidersHorizontal strokeWidth={2} size={18} />,
  calling_blocks: <PhoneOff strokeWidth={2} size={18} />,
  call_monitor: <Activity strokeWidth={2} size={18} />,
  /* Phase 6 Q3 (voice training lab) */
  voice_lab: <Mic strokeWidth={2} size={18} />,
  /* Phase 6: AI administration and the quality workspace */
  ai_admin: <BrainCircuit strokeWidth={2} size={18} />,
  quality: <BadgeCheck strokeWidth={2} size={18} />,
};

export default navigationIcon;
