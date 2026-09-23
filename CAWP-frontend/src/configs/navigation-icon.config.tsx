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
  CircleAlert,
  CalendarClock,
  Handshake,
  ClipboardCheck,
  FilePenLine,
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
};

export default navigationIcon;
