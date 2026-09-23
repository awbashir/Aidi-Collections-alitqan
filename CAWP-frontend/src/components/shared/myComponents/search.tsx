import React from "react";
import { useState } from "react";
import Button from "@/components/ui/Button";
import { HiOutlineSearch } from "react-icons/hi";

function Search({ onOpenChange }) {
  const [open, setOpen] = useState(false);
  const handleClose = () => {
    setOpen(false);
    onOpenChange(false);
  };
  const handleOpen = () => {
    setOpen(true);
    onOpenChange(true);
  };
  return (
    <div>
      <div>
        <div>
          <Button
            icon={<HiOutlineSearch />}
            variant="solid"
            size="sm"
            onClick={open ? handleClose : handleOpen}
          >
            بحث
          </Button>
        </div>
      </div>
    </div>
  );
}

export default Search;
