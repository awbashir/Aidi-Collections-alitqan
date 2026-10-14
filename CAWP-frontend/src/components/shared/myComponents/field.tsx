import React from "react";

import { useState } from "react";
import { useEffect, useRef } from "react";
import debounce from "lodash/debounce";

import Input from "@/components/ui/Input";
import { useTranslation } from "react-i18next";

const MyField = (props) => {
  const { t } = useTranslation();
  // props: filter, labelField, isMulti = true
  const { filter, labelField, type, initialValue } = props;

  // theme: dark or light

  /* `initialValue` (optional): what the field shows when it mounts, e.g. the
   * filter already applied to the list. It is not reported through `filter`. */
  const [value, setValue] = useState(
    initialValue !== undefined && initialValue !== null ? initialValue : []
  );

  const initialRender = useRef(true);

  useEffect(() => {
    if (initialRender.current) {
      initialRender.current = false;
    } else {
      filter(value);
    }
  }, [value]);

  const handleDebounceFn = (value) => setValue(value);
  const debounceFn = debounce(handleDebounceFn, 0);

  return (
    <div className="pt-3 pb-3">
      <div className="w-full h-12">
        <h5 className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">
          {labelField?.title ? t(labelField?.title) : t(labelField?.plural)}
        </h5>
        <Input
          id="outlined-basic"
          variant="outlined"
          value={value}
          name="TextField"
          type={type}
          onChange={(e) => debounceFn(e.target.value)}
          className="w-full border-radius-0"
          size="sm"
        />
      </div>
    </div>
  );
};

export default MyField;
