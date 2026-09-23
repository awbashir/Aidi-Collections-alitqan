/* import { div, span } from "@mui/material";
 */ import React from "react";
import isEqual from "lodash/isEqual";

import { useState } from "react";
import { useEffect, useRef } from "react";
/* import { useTheme } from "@mui/material";
import { tokens } from "../../../theme"; */
import Select from "react-dropdown-select";
import debounce from "lodash/debounce";
import { useConfig } from "@/components/ui/ConfigProvider";

import useDirection from "@/utils/hooks/useDirection";
import { useTranslation } from "react-i18next";

const MyFilter = (props) => {
  const { t } = useTranslation();
  const { themeColor, controlSize, primaryColorLevel } = useConfig();
  const { filter, options, labelField, isMulti = true, loading = false } = props;
  const [direction, updateDirection] = useDirection();

  /* 
  const theme = useTheme();
  const colors = tokens(theme.palette.mode); */

  const [values, setValues] = useState([]);

  function useDeepCompareEffect(callback, dependencies) {
    const previousDependencies = React.useRef(dependencies);

    if (!isEqual(previousDependencies.current, dependencies)) {
      previousDependencies.current = dependencies;
    }

    useEffect(callback, [previousDependencies.current]);
  }

  useDeepCompareEffect(() => {
    setValues([]);
  }, [options]);

  const initialRender = useRef(true);

  useEffect(() => {
    if (initialRender.current) {
      initialRender.current = false;
    } else {
      filter(values);
    }
  }, [values]);

  const handleDebounceFn = (value) => setValues(value);
  const debounceFn = debounce(handleDebounceFn, 500);

  return (
    <div className="pt-3 pb-3">
      <div className="w-full h-12">
        <h5 className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">
          {labelField?.title ? t(labelField?.title) : t(labelField?.plural)}
        </h5>
        <Select
          name="options"
          options={options}
          labelField="name"
          valueField="id"
          multi={isMulti}
          onChange={(values) => debounceFn(values)}
          contentRenderer={({ props, state }) => {
            if (isMulti) {
              if (state.values.length === 0) {
                return <span> {t("select")}</span>;
              }
              if (state.values.length === props.options.length) {
                return (
                  <span>
                    {" "}
                    {t("SelectedAll")} {t(labelField?.plural)}
                  </span>
                );
              }

              return (
                <span>
                  {" "}
                  {t("selected")} {" " + state.values.length} {t("from")}{" "}
                  {props.options.length}
                </span>
              );
            } else {
              if (values.length === 0) {
                return <span> {t("select")}</span>;
              } else {
                return <span>{values[0]?.name}</span>;
              }
            }
          }}
          values={values}
          Searchable
          color="#2F5FD6"
          dropdownHeight="300px"
          searchable="true"
          noDataLabel={t("noOptionsMessage")}
          placeholder={t("noOptionsMessage")}
          style={{
            borderRadius: "6px",
            minHeight: "36px",
            borderColor: "#B8C4CE",
            fontSize: "14px",
          }}
          direction={direction}
          loading={loading}
        ></Select>
      </div>
    </div>
  );
};

export default MyFilter;
