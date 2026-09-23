import { Grid } from "@mui/material";
import { useState } from "react";
import { useEffect, useRef } from "react";
import debounce from "lodash/debounce";
import Input from "@/components/ui/Input";

function formatDate(date) {
  let d = new Date(date),
    month = "" + (d.getMonth() + 1),
    day = "" + d.getDate(),
    year = d.getFullYear();

  if (month.length < 2) month = "0" + month;
  if (day.length < 2) day = "0" + day;

  return [year, month, day].join("-");
}

function FilterDate({ filter, labelField, defulltFrom, defulltTo }) {
  const [values1, setValues1] = useState(
    defulltFrom
      ? formatDate(defulltFrom)
      : new Date().toISOString().slice(0, 10)
  );
  const [values2, setValues2] = useState(
    defulltTo ? formatDate(defulltTo) : new Date().toISOString().slice(0, 10)
  );

  const initialRender = useRef(true);

  useEffect(() => {
    if (initialRender.current) {
      initialRender.current = false;
    } else {
      filter([values1, values2]);
    }
  }, [values1, values2]);

  const handleDebounceFn1 = (value) => setValues1(value);
  const debounceFn1 = debounce(handleDebounceFn1, 1500);

  const handleDebounceFn2 = (value) => setValues2(value);
  const debounceFn2 = debounce(handleDebounceFn2, 1500);

  return (
    <div className="pt-3 pb-3">
      <div className=" grid grid-cols-2 gap-4">
        <div>
          <h5 className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">{labelField} من</h5>
          <Input
            id="outlined-basic"
            variant="outlined"
            value={values1}
            type="date"
            onChange={(e) => debounceFn1(e.target.value)}
            className="w-full border-radius-0"
            size="sm"
          />
        </div>
        <div>
          <h5 className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">الى</h5>
          <Input
            id="outlined-basic"
            variant="outlined"
            value={values2}
            type="date"
            onChange={(e) => debounceFn2(e.target.value)}
            className="w-full border-radius-0"
            size="sm"
          />
        </div>
      </div>
    </div>
  );
}

export default FilterDate;
